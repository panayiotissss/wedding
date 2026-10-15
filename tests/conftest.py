"""
Test setup: a throwaway SQLite database, fake R2 credentials and test Stripe
settings, all set BEFORE the app is imported (config reads env at import).

Nothing here touches the network: R2 listing/deleting is patched per test, and
presigned URLs are signed locally by boto3 without calling R2.
"""

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)  # FileResponse("static/...") paths are relative to the repo root

_db = Path(tempfile.mkdtemp()) / "test.db"

os.environ.update({
    "DATABASE_URL": f"sqlite:///{_db}",
    "SESSION_SECRET": "test-secret",
    "COOKIE_SECURE": "false",
    "R2_ACCESS_KEY_ID": "test",
    "R2_SECRET_ACCESS_KEY": "test",
    "R2_BUCKET": "test-bucket",
    "R2_ENDPOINT": "https://test.r2.cloudflarestorage.com",
    "R2_KEY_ROOT": "staging/events",
    "STRIPE_PAYMENT_LINK_URL": "https://buy.stripe.com/test_link",
    "STRIPE_WEBHOOK_SECRET": "whsec_test_secret",
    "FREE_ACCESS_CODE": "FREE-TEST",
    "EMAIL_DNS_CHECK": "false",
    "CLEANUP_INTERVAL_HOURS": "0",
    "ADMIN_TOKEN": "test-admin",
    "PUBLIC_BASE_URL": "http://testserver",
})

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402
from db import Base, engine  # noqa: E402


@pytest.fixture(autouse=True)
def clean_db():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield


@pytest.fixture
def client():
    with TestClient(main.app) as c:
        yield c


@pytest.fixture
def signup(client):
    """POST /api/events with sensible defaults; override any field."""
    def _signup(**overrides):
        body = {
            "couple_names": "Sofia & Daniel",
            "email": "sofia@example.com",
            "password": "Wedding2026",
        }
        body.update(overrides)
        return client.post("/api/events", json=body)
    return _signup
