"""
Multi-tenant wedding photo-sharing SaaS.

Each couple ("event") gets:
  - a public upload page   /e/<slug>            (guests, gated on payment)
  - a private gallery      /e/<slug>/gallery    (couple, password-gated)
  - isolated storage in R2 under events/<id>/   (WED-5)
  - a dynamic name shown on every page          (WED-4)
  - auto-deletion 1 year after payment          (WED-6)

Onboarding is self-serve + pay:
  /  →  /create  →  Stripe Payment Link  →  webhook activates  →  /create/success
(WED-2 multi-user, WED-7 pay-to-unlock).
"""

import hashlib
import io
import re
import uuid
import zipfile
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, field_validator
from sqlalchemy.orm import Session
from starlette.middleware.sessions import SessionMiddleware

import config
import payments
import storage
from db import Event, get_db, init_db, now_utc
from security import hash_password, verify_password


# ---------------------------------------------------------------------------
# App + middleware
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(docs_url=None, redoc_url=None, lifespan=lifespan)

app.add_middleware(
    SessionMiddleware,
    secret_key=config.SESSION_SECRET,
    session_cookie="wed_session",
    max_age=60 * 60 * 24 * 7,  # 7 days
    https_only=config.COOKIE_SECURE,
    same_site="lax",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def sanitize_name(name: str) -> str:
    name = name.strip().lower()
    name = re.sub(r"\s+", "_", name)
    name = re.sub(r"[^a-z0-9_]", "", name)
    return name[:50] or "guest"


def sanitize_filename(name: str) -> str:
    return re.sub(r"[^a-zA-Z0-9._-]", "_", Path(name).name)[:100]


def slugify_names(name: str) -> str:
    """'Orestis & Athina' -> 'orestis-athina' for a readable R2 folder."""
    s = re.sub(r"[&/]+", " ", name.strip().lower())
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s[:60] or "couple"


def _storage_key(ev: "Event") -> str:
    """The R2 folder for this event: readable prefix, or id for legacy rows."""
    return ev.storage_prefix or ev.id


def get_event_or_404(db: Session, slug: str) -> Event:
    ev = db.query(Event).filter(Event.slug == slug).first()
    if not ev:
        raise HTTPException(status_code=404, detail="Gallery not found")
    return ev


def require_event_auth(request: Request, slug: str) -> None:
    """Raise 401 unless this session has authenticated for this event's gallery."""
    if slug not in request.session.get("auth_events", []):
        raise HTTPException(status_code=401, detail="Not authenticated")


def _grant_auth(request: Request, slug: str) -> None:
    auth = request.session.get("auth_events", [])
    if slug not in auth:
        auth.append(slug)
        request.session["auth_events"] = auth


def _activate(ev: Event) -> None:
    """Mark an event paid & active, and set its 1-year expiry (WED-6)."""
    ev.status = "active"
    ev.paid_at = now_utc()
    ev.expires_at = ev.paid_at + timedelta(days=config.EVENT_TTL_DAYS)


def _unique_slug(db: Session) -> str:
    for _ in range(10):
        slug = uuid.uuid4().hex[:8]
        if not db.query(Event).filter(Event.slug == slug).first():
            return slug
    raise HTTPException(status_code=500, detail="Could not allocate a slug")


# ---------------------------------------------------------------------------
# Page routes (serve static HTML shells; the JS reads the slug from the URL)
# ---------------------------------------------------------------------------

@app.get("/")
def landing_page():
    return FileResponse("static/landing.html")


@app.get("/create")
def create_page():
    return FileResponse("static/create.html")


@app.get("/create/success")
def success_page():
    return FileResponse("static/success.html")


@app.get("/login")
def login_page():
    return FileResponse("static/login.html")


@app.get("/e/{slug}")
def event_upload_page(slug: str):
    return FileResponse("static/event.html")


@app.get("/e/{slug}/gallery")
def event_gallery_page(slug: str):
    return FileResponse("static/gallery.html")


@app.get("/e/{slug}/qr.png")
def event_qr(slug: str, db: Session = Depends(get_db)):
    """Printable QR code pointing at this event's upload page."""
    ev = get_event_or_404(db, slug)
    import qrcode

    url = f"{config.PUBLIC_BASE_URL}/e/{ev.slug}"
    img = qrcode.make(url)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return Response(content=buf.getvalue(), media_type="image/png")


# ---------------------------------------------------------------------------
# Onboarding — create event + payment (WED-2, WED-7)
# ---------------------------------------------------------------------------

def _normalize_email(v: str) -> str:
    return v.strip().lower()


class CreateEventRequest(BaseModel):
    couple_names: str
    password: str
    email: str                       # now REQUIRED — it's the login identifier
    access_code: str | None = None   # optional comp code (skips payment)

    @field_validator("couple_names")
    @classmethod
    def check_names(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Couple names are required")
        return v.strip()[:200]

    @field_validator("password")
    @classmethod
    def check_password(cls, v: str) -> str:
        if len(v) < 6:
            raise ValueError("Password must be at least 6 characters")
        return v

    @field_validator("email")
    @classmethod
    def check_email(cls, v: str) -> str:
        v = v.strip()
        if "@" not in v or "." not in v.split("@")[-1]:
            raise ValueError("A valid email is required")
        return v


@app.post("/api/events")
def create_event(body: CreateEventRequest, request: Request, db: Session = Depends(get_db)):
    email = _normalize_email(body.email)

    # Email is the login identifier, so it must be unique.
    if db.query(Event).filter(Event.contact_email == email).first():
        raise HTTPException(
            status_code=409,
            detail="An account with this email already exists. Please log in instead.",
        )

    slug = _unique_slug(db)
    ev = Event(
        slug=slug,
        couple_names=body.couple_names,
        password_hash=hash_password(body.password),
        contact_email=email,
        status="pending_payment",
        # Readable R2 folder: couple name + slug (slug guarantees uniqueness,
        # since two different couples can share the same names).
        storage_prefix=f"{slugify_names(body.couple_names)}-{slug}",
    )

    # A valid comp code lets a friend skip payment even when Stripe is on.
    code_ok = bool(config.FREE_ACCESS_CODE) and (body.access_code or "").strip() == config.FREE_ACCESS_CODE

    if config.PAYMENTS_ENABLED and not code_ok:
        db.add(ev)
        db.commit()
        _grant_auth(request, ev.slug)  # so the couple lands in their gallery after paying
        request.session["last_slug"] = ev.slug  # so /create/success works after the Stripe round-trip
        return {"slug": ev.slug, "status": ev.status, "payment_url": payments.payment_url(ev)}

    # Free path: stub mode (no Stripe) OR a valid comp code — unlock immediately.
    _activate(ev)
    db.add(ev)
    db.commit()
    _grant_auth(request, ev.slug)
    request.session["last_slug"] = ev.slug
    return {"slug": ev.slug, "status": ev.status, "payment_url": f"/create/success?slug={ev.slug}"}


class EmailLoginRequest(BaseModel):
    email: str
    password: str


@app.post("/api/login")
def email_login(body: EmailLoginRequest, request: Request, db: Session = Depends(get_db)):
    """Couple login by the email + password collected at signup — so they can get
    back into their gallery from any device without needing the original link."""
    email = _normalize_email(body.email)
    ev = db.query(Event).filter(Event.contact_email == email).first()
    if not ev or not verify_password(body.password, ev.password_hash):
        raise HTTPException(status_code=401, detail="Wrong email or password")
    _grant_auth(request, ev.slug)
    request.session["last_slug"] = ev.slug
    return {"slug": ev.slug}


@app.get("/api/my-latest")
def my_latest(request: Request):
    """The gallery this browser session most recently created — lets the
    Stripe success page find it without a slug in the (static) return URL."""
    return {"slug": request.session.get("last_slug")}


@app.get("/api/events/{slug}/status")
def event_status(slug: str, db: Session = Depends(get_db)):
    ev = get_event_or_404(db, slug)
    return {"slug": ev.slug, "status": ev.status, "names": ev.couple_names}


@app.post("/api/stripe/webhook")
async def stripe_webhook(request: Request, db: Session = Depends(get_db)):
    if not config.PAYMENTS_ENABLED:
        raise HTTPException(status_code=404, detail="Payments not configured")

    payload = await request.body()
    sig = request.headers.get("stripe-signature")
    try:
        event = payments.verify_webhook(payload, sig)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid signature")

    if event["type"] == "checkout.session.completed":
        session = event["data"]["object"]
        ref = session.get("client_reference_id")
        if ref:
            ev = db.query(Event).filter(Event.id == ref).first()
            if ev and ev.status != "active":
                _activate(ev)
                ev.stripe_ref = session.get("id")
                db.commit()

    return {"received": True}


# ---------------------------------------------------------------------------
# Per-event public info (WED-4 dynamic name + paywall state)
# ---------------------------------------------------------------------------

@app.get("/api/e/{slug}/info")
def event_info(slug: str, db: Session = Depends(get_db)):
    ev = get_event_or_404(db, slug)
    return {"slug": ev.slug, "names": ev.couple_names, "active": ev.status == "active"}


# ---------------------------------------------------------------------------
# Guest upload (PUBLIC, gated on payment)
# ---------------------------------------------------------------------------

class UploadRequest(BaseModel):
    filename: str
    content_type: str
    guest_name: str
    file_size: int

    @field_validator("content_type")
    @classmethod
    def check_type(cls, v: str) -> str:
        if v not in config.ALLOWED_TYPES:
            raise ValueError(f"Not an allowed file type: {v}")
        return v

    @field_validator("file_size")
    @classmethod
    def check_size(cls, v: int) -> int:
        if v > config.MAX_FILE_SIZE:
            raise ValueError(f"File exceeds {config.MAX_FILE_SIZE // 1024 // 1024} MB limit")
        return v

    @field_validator("guest_name")
    @classmethod
    def check_name(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Guest name is required")
        return v


@app.post("/api/e/{slug}/upload-url")
def get_upload_url(slug: str, body: UploadRequest, db: Session = Depends(get_db)):
    ev = get_event_or_404(db, slug)
    if ev.status != "active":
        raise HTTPException(status_code=403, detail="This gallery isn't active yet.")

    key = (
        storage.photos_prefix(_storage_key(ev))
        + f"{sanitize_name(body.guest_name)}/{uuid.uuid4()}-{sanitize_filename(body.filename)}"
    )
    return {"url": storage.presign_put(key, body.content_type), "key": key}


# ---------------------------------------------------------------------------
# Couple login / logout (per-event password)
# ---------------------------------------------------------------------------

class LoginRequest(BaseModel):
    password: str


@app.post("/api/e/{slug}/login")
def login(slug: str, body: LoginRequest, request: Request, db: Session = Depends(get_db)):
    ev = get_event_or_404(db, slug)
    if not verify_password(body.password, ev.password_hash):
        raise HTTPException(status_code=401, detail="Wrong password")
    _grant_auth(request, slug)
    return {"ok": True}


@app.post("/api/e/{slug}/logout")
def logout(slug: str, request: Request):
    auth = request.session.get("auth_events", [])
    request.session["auth_events"] = [s for s in auth if s != slug]
    return {"ok": True}


@app.get("/api/e/{slug}/me")
def me(slug: str, request: Request):
    return {"authenticated": slug in request.session.get("auth_events", [])}


# ---------------------------------------------------------------------------
# Gallery — photo list (PROTECTED)
# ---------------------------------------------------------------------------

@app.get("/api/e/{slug}/photos")
def list_photos(slug: str, request: Request, db: Session = Depends(get_db)):
    ev = get_event_or_404(db, slug)
    require_event_auth(request, slug)

    prefix = storage.photos_prefix(_storage_key(ev))
    objects = storage.list_prefix(prefix)
    objects.sort(key=lambda o: o["LastModified"])

    guests: dict[str, list] = {}
    for obj in objects:
        rel = obj["Key"][len(prefix):]          # <guest>/<file>
        parts = rel.split("/")
        if len(parts) < 2:
            continue
        guest = parts[0]
        guests.setdefault(guest, []).append(
            {
                "key": obj["Key"],
                "url": storage.presign_get(obj["Key"]),
                "download_url": storage.presign_get(obj["Key"], download_as=parts[-1]),
                "uploaded_at": obj["LastModified"].isoformat(),
                "filename": parts[-1],
            }
        )

    return [{"guest": g, "photos": photos} for g, photos in guests.items()]


# ---------------------------------------------------------------------------
# Gallery — download all as ZIP (PROTECTED, served from R2)
# ---------------------------------------------------------------------------

ZIP_NAME = "wedding_photos.zip"


def _photos_signature(objects: list[dict]) -> str:
    """Fingerprint of the current photo set; changes on any upload or delete."""
    h = hashlib.sha256()
    for o in sorted(objects, key=lambda o: o["Key"]):
        h.update(f"{o['Key']}|{o.get('ETag', '')}\n".encode())
    return h.hexdigest()[:32]


def _build_zip(objects: list[dict], prefix: str, zip_key: str, sig: str) -> None:
    """Copy every photo into one zip object in R2 (multipart, streamed)."""
    writer = storage.MultipartWriter(zip_key, "application/zip", metadata={"sig": sig})
    try:
        with zipfile.ZipFile(writer, "w", zipfile.ZIP_STORED) as zf:
            for obj in objects:
                rel = obj["Key"][len(prefix):]
                parts = rel.split("/")
                guest = parts[0] if len(parts) > 1 else "guest"
                zinfo = zipfile.ZipInfo(f"{guest}/{parts[-1]}")
                zinfo.compress_type = zipfile.ZIP_STORED
                with zf.open(zinfo, mode="w", force_zip64=True) as dest:
                    body = storage.get_body(obj["Key"])
                    for chunk in body.iter_chunks(chunk_size=1024 * 1024):
                        dest.write(chunk)
        writer.complete()
    except BaseException:
        writer.abort()
        raise


@app.get("/api/e/{slug}/download-zip")
def download_zip(slug: str, request: Request, db: Session = Depends(get_db)):
    """WED-9: the browser downloads the zip straight from R2.

    The zip is built once into R2 under exports/ and reused until the photo set
    changes, then we redirect to a short-lived presigned link. The couple's
    download no longer streams through this server.
    """
    ev = get_event_or_404(db, slug)
    require_event_auth(request, slug)
    key = _storage_key(ev)
    prefix = storage.photos_prefix(key)
    zip_key = storage.exports_prefix(key) + ZIP_NAME

    objects = storage.list_prefix(prefix)
    if not objects:
        raise HTTPException(status_code=404, detail="No photos yet")

    sig = _photos_signature(objects)
    existing = storage.head(zip_key)
    if not existing or existing.get("Metadata", {}).get("sig") != sig:
        _build_zip(objects, prefix, zip_key, sig)

    return RedirectResponse(storage.presign_get(zip_key, download_as=ZIP_NAME), status_code=302)


# ---------------------------------------------------------------------------
# Gallery — delete a photo (PROTECTED)
# ---------------------------------------------------------------------------

class DeleteRequest(BaseModel):
    key: str


@app.post("/api/e/{slug}/delete")
def delete_photo(slug: str, body: DeleteRequest, request: Request, db: Session = Depends(get_db)):
    ev = get_event_or_404(db, slug)
    require_event_auth(request, slug)

    # Refuse any key outside THIS event's photo prefix.
    expected = storage.photos_prefix(_storage_key(ev))
    if not body.key.startswith(expected) or ".." in body.key:
        raise HTTPException(status_code=400, detail="Invalid key")
    storage.delete_key(body.key)
    return {"ok": True}


# ---------------------------------------------------------------------------
# Admin — auto-delete expired events (WED-6)
# ---------------------------------------------------------------------------

@app.post("/api/admin/cleanup")
def cleanup(request: Request, db: Session = Depends(get_db)):
    """Delete every event past its expiry, plus all its R2 objects.

    Protect with the ADMIN_TOKEN env var and call on a schedule (Render Cron /
    external cron / GitHub Action):  curl -X POST -H 'X-Admin-Token: ...' /api/admin/cleanup
    """
    if not config.ADMIN_TOKEN or request.headers.get("x-admin-token") != config.ADMIN_TOKEN:
        raise HTTPException(status_code=401, detail="Unauthorized")

    now = now_utc()
    expired = (
        db.query(Event)
        .filter(Event.expires_at.isnot(None), Event.expires_at < now)
        .all()
    )

    deleted: list[str] = []
    for ev in expired:
        for obj in storage.list_prefix(storage.event_root(_storage_key(ev))):
            storage.delete_key(obj["Key"])
        db.delete(ev)
        deleted.append(ev.slug)
    db.commit()

    return {"deleted": deleted, "count": len(deleted)}


# ---------------------------------------------------------------------------
# Static assets
# ---------------------------------------------------------------------------

app.mount("/static", StaticFiles(directory="static"), name="static")
