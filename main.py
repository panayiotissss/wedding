"""
Wedding photo-sharing app.

Two roles:
  - Guests   → POST /api/upload-url  (public)
  - Couple   → POST /api/login, GET /api/photos, GET /api/download-zip (session-gated)
"""

import asyncio
import io
import os
import re
import uuid
import zipfile
from pathlib import Path

import boto3
from botocore.config import Config
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, field_validator
from starlette.middleware.sessions import SessionMiddleware

# ---------------------------------------------------------------------------
# Config — everything comes from env vars so secrets never touch the code.
# ---------------------------------------------------------------------------

load_dotenv()

def _require(name: str) -> str:
    v = os.environ.get(name)
    if not v:
        raise RuntimeError(f"Missing required env var: {name}")
    return v

R2_ACCOUNT_ID       = _require("R2_ACCOUNT_ID")
R2_ACCESS_KEY_ID    = _require("R2_ACCESS_KEY_ID")
R2_SECRET_ACCESS_KEY = _require("R2_SECRET_ACCESS_KEY")
R2_BUCKET           = _require("R2_BUCKET")
R2_ENDPOINT         = _require("R2_ENDPOINT")
GALLERY_PASSWORD    = _require("GALLERY_PASSWORD")
SESSION_SECRET      = _require("SESSION_SECRET")

MAX_FILE_SIZE = 200 * 1024 * 1024  # 200 MB — generous for videos

# These are the only content-types we'll presign for.
# R2 enforces the type matches because ContentType is baked into the presigned URL.
ALLOWED_TYPES = {
    "image/jpeg",
    "image/png",
    "image/gif",
    "image/webp",
    "image/heic",   # iOS HEIC
    "image/heif",   # iOS HEIF variant
    "video/mp4",
    "video/quicktime",  # .mov
    "video/x-msvideo",  # .avi
}

# ---------------------------------------------------------------------------
# R2 client  (boto3 understands any S3-compatible endpoint)
# ---------------------------------------------------------------------------

s3 = boto3.client(
    "s3",
    endpoint_url=R2_ENDPOINT,
    aws_access_key_id=R2_ACCESS_KEY_ID,
    aws_secret_access_key=R2_SECRET_ACCESS_KEY,
    region_name="auto",
    config=Config(signature_version="s3v4"),
)

# ---------------------------------------------------------------------------
# App + middleware
# ---------------------------------------------------------------------------

app = FastAPI(docs_url=None, redoc_url=None)  # hide docs in production

# SessionMiddleware stores a signed JSON blob in a cookie.
# The SESSION_SECRET is the HMAC signing key — keep it secret.
app.add_middleware(
    SessionMiddleware,
    secret_key=SESSION_SECRET,
    session_cookie="wed_session",
    max_age=60 * 60 * 24 * 7,  # 7 days
    https_only=False,           # set True when behind HTTPS in production
    same_site="lax",
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def sanitize_name(name: str) -> str:
    """Turn a guest name into a safe S3 key segment."""
    name = name.strip().lower()
    name = re.sub(r"\s+", "_", name)          # spaces → underscores
    name = re.sub(r"[^a-z0-9_]", "", name)   # drop everything else
    return name[:50] or "guest"


def sanitize_filename(name: str) -> str:
    """Strip path separators and dangerous chars from an uploaded filename."""
    return re.sub(r"[^a-zA-Z0-9._-]", "_", Path(name).name)[:100]


def require_auth(request: Request) -> None:
    """FastAPI dependency — raises 401 if the session cookie isn't valid."""
    if not request.session.get("authenticated"):
        raise HTTPException(status_code=401, detail="Not authenticated")


# ---------------------------------------------------------------------------
# Page routes  (serve HTML files from ./static/)
# ---------------------------------------------------------------------------

@app.get("/")
def upload_page():
    return FileResponse("static/index.html")


@app.get("/gallery")
def gallery_page():
    return FileResponse("static/gallery.html")


# ---------------------------------------------------------------------------
# API — upload presigned URL  (PUBLIC, guests call this)
# ---------------------------------------------------------------------------

class UploadRequest(BaseModel):
    filename: str
    content_type: str
    guest_name: str
    file_size: int

    @field_validator("content_type")
    @classmethod
    def check_type(cls, v: str) -> str:
        if v not in ALLOWED_TYPES:
            raise ValueError(f"Not an allowed file type: {v}")
        return v

    @field_validator("file_size")
    @classmethod
    def check_size(cls, v: int) -> int:
        if v > MAX_FILE_SIZE:
            raise ValueError(f"File exceeds {MAX_FILE_SIZE // 1024 // 1024} MB limit")
        return v

    @field_validator("guest_name")
    @classmethod
    def check_name(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Guest name is required")
        return v


@app.post("/api/upload-url")
def get_upload_url(body: UploadRequest):
    """
    Return a presigned PUT URL the browser can use to upload directly to R2.

    Why presigned URLs?
    The browser sends the file straight to R2 — our server is never in the data
    path.  R2 enforces ContentType because we bake it into the presigned params,
    preventing type-spoofing attacks.
    """
    safe_name = sanitize_name(body.guest_name)
    safe_file = sanitize_filename(body.filename)
    key = f"photos/{safe_name}/{uuid.uuid4()}-{safe_file}"

    # The presigned URL encodes: Bucket, Key, ContentType, and an expiry.
    # R2 will reject any PUT that doesn't match those params exactly.
    presigned_url = s3.generate_presigned_url(
        "put_object",
        Params={
            "Bucket": R2_BUCKET,
            "Key": key,
            "ContentType": body.content_type,
        },
        ExpiresIn=3600,  # 1 hour — plenty of time for the upload
    )

    return {"url": presigned_url, "key": key}


# ---------------------------------------------------------------------------
# API — login / logout
# ---------------------------------------------------------------------------

class LoginRequest(BaseModel):
    password: str


@app.post("/api/login")
def login(body: LoginRequest, request: Request):
    """
    Check password against GALLERY_PASSWORD env var.
    On success, set authenticated=True in the signed session cookie.
    No user table, no JWT — just a single shared secret for the couple.
    """
    if body.password != GALLERY_PASSWORD:
        raise HTTPException(status_code=401, detail="Wrong password")
    request.session["authenticated"] = True
    return {"ok": True}


@app.post("/api/logout")
def logout(request: Request):
    request.session.clear()
    return {"ok": True}


@app.get("/api/me")
def me(request: Request):
    """Quick check so the frontend can tell if a session is still valid."""
    return {"authenticated": bool(request.session.get("authenticated"))}


# ---------------------------------------------------------------------------
# API — photo list  (PROTECTED)
# ---------------------------------------------------------------------------

@app.get("/api/photos")
def list_photos(_: None = Depends(require_auth)):
    """
    List the bucket, group by guest, return presigned GET URLs.

    Why presigned GETs?
    The bucket stays private — we never make objects public.  The frontend
    receives time-limited signed URLs that R2 validates per-request.
    """
    paginator = s3.get_paginator("list_objects_v2")
    objects: list[dict] = []
    for page in paginator.paginate(Bucket=R2_BUCKET, Prefix="photos/"):
        objects.extend(page.get("Contents", []))

    # Sort oldest → newest so gallery is chronological
    objects.sort(key=lambda o: o["LastModified"])

    guests: dict[str, list] = {}
    for obj in objects:
        parts = obj["Key"].split("/")
        if len(parts) < 3:
            continue  # malformed key, skip
        guest = parts[1]
        url = s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": R2_BUCKET, "Key": obj["Key"]},
            ExpiresIn=3600,
        )
        guests.setdefault(guest, []).append(
            {
                "key": obj["Key"],
                "url": url,
                "uploaded_at": obj["LastModified"].isoformat(),
                "filename": parts[-1],
            }
        )

    # Return as list of {guest, photos} for easy iteration in the frontend
    return [
        {"guest": g, "photos": photos} for g, photos in guests.items()
    ]


# ---------------------------------------------------------------------------
# API — download all as ZIP  (PROTECTED)
# ---------------------------------------------------------------------------

@app.get("/api/download-zip")
async def download_zip(_: None = Depends(require_auth)):
    """
    Pull every object from R2, pack into a zip, stream to the browser.

    Why on the server?
    The bucket is private so the browser can't access objects directly without
    presigned URLs.  Building the zip here is the cleanest single-endpoint
    download for the couple.

    Trade-off: the server loads all photos into RAM while building the zip.
    For a wedding (a few hundred photos) this is fine.  A 500-photo wedding
    at 5 MB/photo = 2.5 GB — make sure your server has enough memory, or
    upgrade to a streaming zip library if needed.
    """
    def build_zip() -> bytes:
        paginator = s3.get_paginator("list_objects_v2")
        objects: list[dict] = []
        for page in paginator.paginate(Bucket=R2_BUCKET, Prefix="photos/"):
            objects.extend(page.get("Contents", []))

        buf = io.BytesIO()
        # ZIP_STORED skips re-compression — images/videos are already compressed.
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as zf:
            for obj in objects:
                key = obj["Key"]
                parts = key.split("/")
                guest = parts[1] if len(parts) > 2 else "unknown"
                filename = parts[-1]
                body = s3.get_object(Bucket=R2_BUCKET, Key=key)["Body"].read()
                zf.writestr(f"{guest}/{filename}", body)

        buf.seek(0)
        return buf.read()

    # Run the blocking boto3 calls in a thread so FastAPI stays responsive
    data = await asyncio.to_thread(build_zip)

    return Response(
        content=data,
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="wedding_photos.zip"'},
    )


# ---------------------------------------------------------------------------
# Static files (CSS / JS assets if you ever add them)
# ---------------------------------------------------------------------------

app.mount("/static", StaticFiles(directory="static"), name="static")
