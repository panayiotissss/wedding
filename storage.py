"""
R2 (S3-compatible) storage helpers.

WED-5: every event's media is isolated under its own key prefix
    <R2_KEY_ROOT>/<event_id>/photos/<guest>/<uuid>-<filename>
so one cheap bucket serves all couples with no cross-event access.
R2_KEY_ROOT is "events" in production and "staging/events" on staging.
"""

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

import config

s3 = boto3.client(
    "s3",
    endpoint_url=config.R2_ENDPOINT,
    aws_access_key_id=config.R2_ACCESS_KEY_ID,
    aws_secret_access_key=config.R2_SECRET_ACCESS_KEY,
    region_name="auto",
    config=Config(signature_version="s3v4"),
)


def event_root(key: str) -> str:
    """Top-level prefix for everything belonging to an event.

    `key` is the event's storage_prefix (human-readable) or, for older rows,
    its id — the caller decides via main._storage_key().
    """
    return f"{config.R2_KEY_ROOT}/{key}/"


def photos_prefix(key: str) -> str:
    return f"{config.R2_KEY_ROOT}/{key}/photos/"


def presign_put(key: str, content_type: str, expires: int = 3600) -> str:
    return s3.generate_presigned_url(
        "put_object",
        Params={"Bucket": config.R2_BUCKET, "Key": key, "ContentType": content_type},
        ExpiresIn=expires,
    )


def presign_get(key: str, expires: int = 3600, download_as: str | None = None) -> str:
    """Presigned GET.  With `download_as`, R2 serves the object with
    Content-Disposition: attachment so the browser saves it instead of
    opening it (the <a download> attribute is ignored cross-origin)."""
    params = {"Bucket": config.R2_BUCKET, "Key": key}
    if download_as:
        params["ResponseContentDisposition"] = f'attachment; filename="{download_as}"'
    return s3.generate_presigned_url("get_object", Params=params, ExpiresIn=expires)


def exports_prefix(key: str) -> str:
    """Generated files (e.g. the gallery zip). Outside photos/ so the gallery
    never lists them; still under event_root so cleanup deletes them."""
    return f"{config.R2_KEY_ROOT}/{key}/exports/"


def head(key: str) -> dict | None:
    try:
        return s3.head_object(Bucket=config.R2_BUCKET, Key=key)
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
            return None
        raise


class MultipartWriter:
    """Write-only file object that streams into an R2 object via multipart
    upload, so a multi-GB zip never sits in memory or on local disk.

    zipfile treats it as unseekable (no tell/seek) and writes data descriptors.
    """

    PART_SIZE = 8 * 1024 * 1024  # S3/R2 minimum is 5 MiB for all but the last part

    def __init__(self, key: str, content_type: str, metadata: dict[str, str] | None = None):
        self.key = key
        self._buf = bytearray()
        self._parts: list[dict] = []
        self._upload_id = s3.create_multipart_upload(
            Bucket=config.R2_BUCKET, Key=key, ContentType=content_type,
            Metadata=metadata or {},
        )["UploadId"]

    def write(self, data: bytes) -> int:
        self._buf += data
        while len(self._buf) >= self.PART_SIZE:
            self._upload(bytes(self._buf[: self.PART_SIZE]))
            del self._buf[: self.PART_SIZE]
        return len(data)

    def flush(self) -> None:
        pass

    def _upload(self, chunk: bytes) -> None:
        n = len(self._parts) + 1
        resp = s3.upload_part(
            Bucket=config.R2_BUCKET, Key=self.key, UploadId=self._upload_id,
            PartNumber=n, Body=chunk,
        )
        self._parts.append({"PartNumber": n, "ETag": resp["ETag"]})

    def complete(self) -> None:
        if self._buf or not self._parts:
            self._upload(bytes(self._buf))
            self._buf.clear()
        s3.complete_multipart_upload(
            Bucket=config.R2_BUCKET, Key=self.key, UploadId=self._upload_id,
            MultipartUpload={"Parts": self._parts},
        )

    def abort(self) -> None:
        s3.abort_multipart_upload(Bucket=config.R2_BUCKET, Key=self.key, UploadId=self._upload_id)


def list_prefix(prefix: str) -> list[dict]:
    paginator = s3.get_paginator("list_objects_v2")
    objects: list[dict] = []
    for page in paginator.paginate(Bucket=config.R2_BUCKET, Prefix=prefix):
        objects.extend(page.get("Contents", []))
    return objects


def delete_key(key: str) -> None:
    s3.delete_object(Bucket=config.R2_BUCKET, Key=key)


def delete_prefix(prefix: str) -> int:
    """Delete every object under `prefix`, 1000 keys per call (the S3/R2 cap)."""
    deleted = 0
    keys = [{"Key": o["Key"]} for o in list_prefix(prefix)]
    for i in range(0, len(keys), 1000):
        batch = keys[i : i + 1000]
        s3.delete_objects(Bucket=config.R2_BUCKET, Delete={"Objects": batch, "Quiet": True})
        deleted += len(batch)
    return deleted


def get_body(key: str):
    """Return the streaming Body for one object (used by the zip stream)."""
    return s3.get_object(Bucket=config.R2_BUCKET, Key=key)["Body"]
