"""
R2 (S3-compatible) storage helpers.

WED-5: every event's media is isolated under its own key prefix
    events/<event_id>/photos/<guest>/<uuid>-<filename>
so one cheap bucket serves all couples with no cross-event access.
"""

import boto3
from botocore.config import Config

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
    return f"events/{key}/"


def photos_prefix(key: str) -> str:
    return f"events/{key}/photos/"


def presign_put(key: str, content_type: str, expires: int = 3600) -> str:
    return s3.generate_presigned_url(
        "put_object",
        Params={"Bucket": config.R2_BUCKET, "Key": key, "ContentType": content_type},
        ExpiresIn=expires,
    )


def presign_get(key: str, expires: int = 3600) -> str:
    return s3.generate_presigned_url(
        "get_object",
        Params={"Bucket": config.R2_BUCKET, "Key": key},
        ExpiresIn=expires,
    )


def list_prefix(prefix: str) -> list[dict]:
    paginator = s3.get_paginator("list_objects_v2")
    objects: list[dict] = []
    for page in paginator.paginate(Bucket=config.R2_BUCKET, Prefix=prefix):
        objects.extend(page.get("Contents", []))
    return objects


def delete_key(key: str) -> None:
    s3.delete_object(Bucket=config.R2_BUCKET, Key=key)


def get_body(key: str):
    """Return the streaming Body for one object (used by the zip stream)."""
    return s3.get_object(Bucket=config.R2_BUCKET, Key=key)["Body"]
