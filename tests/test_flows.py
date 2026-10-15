"""
The four checks done by hand on staging, as automated tests, plus the
signup rules around them. Run with:  pytest -q
"""

import hashlib
import hmac
import json
import time
from datetime import datetime, timezone

import config
import storage
from db import Event, SessionLocal


def _event(slug):
    with SessionLocal() as db:
        return db.query(Event).filter(Event.slug == slug).first()


def _stripe_webhook(client, payload: dict, secret: str = "whsec_test_secret"):
    """Send a webhook signed exactly the way Stripe signs it."""
    body = json.dumps(payload)
    ts = int(time.time())
    sig = hmac.new(secret.encode(), f"{ts}.{body}".encode(), hashlib.sha256).hexdigest()
    return client.post(
        "/api/stripe/webhook",
        content=body,
        headers={"stripe-signature": f"t={ts},v1={sig}", "content-type": "application/json"},
    )


def _checkout_completed(event_id: str) -> dict:
    return {
        "id": "evt_test",
        "object": "event",
        "type": "checkout.session.completed",
        "data": {"object": {"id": "cs_test_123", "object": "checkout.session",
                            "client_reference_id": event_id}},
    }


# ── Test 1: paid signup ─────────────────────────────────────────────────────

def test_paid_signup_goes_to_stripe_then_webhook_activates(client, signup):
    res = signup()
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "pending_payment"

    ev = _event(data["slug"])
    assert data["payment_url"].startswith(config.STRIPE_PAYMENT_LINK_URL)
    assert f"client_reference_id={ev.id}" in data["payment_url"]

    # Guests can't upload until it's paid.
    up = client.post(f"/api/e/{ev.slug}/upload-url", json={
        "filename": "a.jpg", "content_type": "image/jpeg", "guest_name": "Eleni", "file_size": 10})
    assert up.status_code == 403

    assert _stripe_webhook(client, _checkout_completed(ev.id)).status_code == 200

    ev = _event(data["slug"])
    assert ev.status == "active"
    assert ev.stripe_ref == "cs_test_123"
    assert ev.expires_at is not None
    assert client.get(f"/api/events/{ev.slug}/status").json()["status"] == "active"


def test_webhook_with_bad_signature_is_rejected(client, signup):
    ev = _event(signup().json()["slug"])
    res = _stripe_webhook(client, _checkout_completed(ev.id), secret="whsec_wrong")
    assert res.status_code == 400
    assert _event(ev.slug).status == "pending_payment"


# ── Test 2: free access code ────────────────────────────────────────────────

def test_free_access_code_skips_payment(client, signup):
    res = signup(access_code="FREE-TEST")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "active"
    assert data["payment_url"] == f"/create/success?slug={data['slug']}"
    assert _event(data["slug"]).paid_at is not None


# ── Test 3: wrong access code ───────────────────────────────────────────────

def test_wrong_access_code_is_rejected_and_nothing_is_created(client, signup):
    res = signup(access_code="NOPE")
    assert res.status_code == 400
    assert "access code" in res.json()["detail"]
    with SessionLocal() as db:
        assert db.query(Event).count() == 0


# ── Test 4: guest upload and the couple's gallery ──────────────────────────

def test_upload_url_and_gallery_listing(client, signup, monkeypatch):
    slug = signup(access_code="FREE-TEST").json()["slug"]
    ev = _event(slug)

    up = client.post(f"/api/e/{slug}/upload-url", json={
        "filename": "IMG 1.jpg", "content_type": "image/jpeg",
        "guest_name": "Eleni K", "file_size": 1024})
    assert up.status_code == 200
    key = up.json()["key"]
    # Stored under this event's own folder, under the staging root.
    assert key.startswith(f"staging/events/{ev.storage_prefix}/photos/eleni_k/")
    assert up.json()["url"].startswith("https://")

    # The gallery lists what's in R2, grouped by guest (R2 itself is faked).
    monkeypatch.setattr(storage, "list_prefix", lambda prefix: [
        {"Key": key, "LastModified": datetime(2026, 10, 15, tzinfo=timezone.utc), "ETag": "x"},
    ])
    photos = client.get(f"/api/e/{slug}/photos")
    assert photos.status_code == 200  # signup logged this browser in
    groups = photos.json()
    assert groups[0]["guest"] == "eleni_k"
    assert groups[0]["photos"][0]["key"] == key


def test_gallery_needs_login(client, signup):
    slug = signup(access_code="FREE-TEST").json()["slug"]
    client.post(f"/api/e/{slug}/logout")
    assert client.get(f"/api/e/{slug}/photos").status_code == 401


def test_upload_rejects_wrong_type_and_oversize(client, signup):
    slug = signup(access_code="FREE-TEST").json()["slug"]
    base = {"filename": "a", "guest_name": "Eleni"}
    bad_type = client.post(f"/api/e/{slug}/upload-url",
                           json={**base, "content_type": "application/pdf", "file_size": 10})
    too_big = client.post(f"/api/e/{slug}/upload-url",
                          json={**base, "content_type": "image/jpeg",
                                "file_size": config.MAX_FILE_SIZE + 1})
    assert bad_type.status_code == 422
    assert too_big.status_code == 422


def test_delete_refuses_keys_outside_the_event(client, signup, monkeypatch):
    slug = signup(access_code="FREE-TEST").json()["slug"]
    deleted = []
    monkeypatch.setattr(storage, "delete_key", deleted.append)
    res = client.post(f"/api/e/{slug}/delete", json={"key": "staging/events/someone-else/photos/x.jpg"})
    assert res.status_code == 400
    assert deleted == []


# ── Signup rules ────────────────────────────────────────────────────────────

def test_back_from_checkout_then_access_code_reuses_the_signup(client, signup):
    """Went to Stripe, pressed Back, entered a code with the same email."""
    first = signup().json()
    assert first["status"] == "pending_payment"

    second = signup(access_code="FREE-TEST", couple_names="Sofia & Dan")
    assert second.status_code == 200
    assert second.json()["slug"] == first["slug"]  # same gallery, no duplicate
    ev = _event(first["slug"])
    assert ev.status == "active"
    assert ev.couple_names == "Sofia & Dan"
    with SessionLocal() as db:
        assert db.query(Event).count() == 1


def test_paid_email_cannot_sign_up_again(client, signup):
    signup(access_code="FREE-TEST")
    assert signup().status_code == 409


def test_email_login_ignores_case(client, signup):
    signup(access_code="FREE-TEST", email="Sofia@Example.com")
    res = client.post("/api/login", json={"email": "SOFIA@example.COM", "password": "Wedding2026"})
    assert res.status_code == 200


def test_weak_passwords_are_rejected(signup):
    assert signup(password="short1").status_code == 422          # under 8
    assert signup(password="onlyletters").status_code == 422     # no number
    assert signup(password="12345678").status_code == 422        # no letter
    assert signup(password="Wedding2026").status_code == 200


def test_invalid_email_syntax_is_rejected(signup):
    assert signup(email="not-an-email").status_code == 422
    assert signup(email="a@b").status_code == 422


def test_email_with_missing_domain_is_rejected(signup, monkeypatch):
    import emailcheck
    monkeypatch.setattr(config, "EMAIL_DNS_CHECK", True)
    monkeypatch.setattr(emailcheck, "_domain_status",
                        lambda d: "exists" if d == "gmail.com" else "missing")
    res = signup(email="fefefef@gmai1l.com")
    assert res.status_code == 422
    assert "gmai1l.com" in res.text


def test_email_check_fails_open_when_dns_is_down(signup, monkeypatch):
    import emailcheck
    monkeypatch.setattr(config, "EMAIL_DNS_CHECK", True)
    monkeypatch.setattr(emailcheck, "_domain_status", lambda d: "missing")  # even gmail "missing"
    assert signup(email="sofia@example.com").status_code == 200


# ── Pages ───────────────────────────────────────────────────────────────────

def test_pages_and_favicon_load(client):
    for path in ["/", "/create", "/login", "/create/success", "/terms", "/privacy"]:
        assert client.get(path).status_code == 200, path
    fav = client.get("/favicon.ico")
    assert fav.status_code == 200
    assert fav.headers["content-type"].startswith("image/svg+xml")


def test_no_em_dashes_in_pages():
    from pathlib import Path
    for page in Path("static").glob("*.html"):
        assert "—" not in page.read_text(encoding="utf-8"), page.name


# ── Emails: welcome + forgot password (WED-21) ─────────────────────────────

import mailer  # noqa: E402
from datetime import timedelta  # noqa: E402
from db import PasswordReset  # noqa: E402


def _capture_emails(monkeypatch):
    sent = []
    monkeypatch.setattr(mailer, "send",
                        lambda to, subject, html, text: sent.append(
                            {"to": to, "subject": subject, "text": text}) or True)
    return sent


def _reset_token(sent):
    import re
    return re.search(r"token=([\w-]+)", sent[-1]["text"]).group(1)


def test_welcome_email_after_free_code_and_after_payment(client, signup, monkeypatch):
    sent = _capture_emails(monkeypatch)
    signup(access_code="FREE-TEST")
    assert sent[-1]["to"] == "sofia@example.com"
    assert "/gallery" in sent[-1]["text"]

    ev = _event(signup(email="other@example.com").json()["slug"])
    assert len(sent) == 1  # nothing yet: unpaid
    _stripe_webhook(client, _checkout_completed(ev.id))
    assert sent[-1]["to"] == "other@example.com"


def test_forgot_password_full_flow(client, signup, monkeypatch):
    sent = _capture_emails(monkeypatch)
    slug = signup(access_code="FREE-TEST").json()["slug"]
    sent.clear()

    assert client.post("/api/password/forgot", json={"email": "SOFIA@example.com"}).status_code == 200
    assert sent and sent[-1]["subject"].startswith("Reset")
    token = _reset_token(sent)

    client.post(f"/api/e/{slug}/logout")
    res = client.post("/api/password/reset", json={"token": token, "password": "NewPass2027"})
    assert res.status_code == 200 and res.json()["slug"] == slug
    assert client.get(f"/api/e/{slug}/me").json()["authenticated"]  # reset logs you back in

    login = lambda pw: client.post("/api/login", json={"email": "sofia@example.com", "password": pw})
    assert login("NewPass2027").status_code == 200
    assert login("Wedding2026").status_code == 401

    # Single use.
    again = client.post("/api/password/reset", json={"token": token, "password": "Another2028"})
    assert again.status_code == 400


def test_forgot_password_unknown_email_looks_the_same(client, monkeypatch):
    sent = _capture_emails(monkeypatch)
    res = client.post("/api/password/forgot", json={"email": "nobody@example.com"})
    assert res.status_code == 200 and res.json() == {"ok": True}
    assert sent == []


def test_reset_link_expires(client, signup, monkeypatch):
    sent = _capture_emails(monkeypatch)
    signup(access_code="FREE-TEST")
    client.post("/api/password/forgot", json={"email": "sofia@example.com"})
    token = _reset_token(sent)
    with SessionLocal() as db:
        pr = db.query(PasswordReset).first()
        pr.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        db.commit()
    res = client.post("/api/password/reset", json={"token": token, "password": "NewPass2027"})
    assert res.status_code == 400


def test_reset_rejects_weak_password_and_bad_token(client, signup, monkeypatch):
    sent = _capture_emails(monkeypatch)
    signup(access_code="FREE-TEST")
    client.post("/api/password/forgot", json={"email": "sofia@example.com"})
    token = _reset_token(sent)
    assert client.post("/api/password/reset", json={"token": token, "password": "short"}).status_code == 422
    assert client.post("/api/password/reset", json={"token": "nope", "password": "NewPass2027"}).status_code == 400


def test_forgot_password_is_rate_limited(client, signup, monkeypatch):
    sent = _capture_emails(monkeypatch)
    signup(access_code="FREE-TEST")
    sent.clear()
    for _ in range(3):
        client.post("/api/password/forgot", json={"email": "sofia@example.com"})
    assert len(sent) == 1


def test_forgot_and_reset_pages_load(client):
    assert client.get("/forgot").status_code == 200
    assert client.get("/reset?token=x").status_code == 200


# ── Auto-delete (WED-6, promised in the Privacy Policy) ────────────────────

def test_cleanup_deletes_expired_and_old_unpaid_only(client, signup, monkeypatch):
    from datetime import timedelta
    wiped = []
    monkeypatch.setattr(storage, "delete_prefix", lambda prefix: wiped.append(prefix) or 0)

    keep_paid = signup(access_code="FREE-TEST", email="keep@example.com").json()["slug"]
    old_paid = signup(access_code="FREE-TEST", email="old@example.com").json()["slug"]
    keep_unpaid = signup(email="fresh@example.com").json()["slug"]
    old_unpaid = signup(email="stale@example.com").json()["slug"]

    now = datetime.now(timezone.utc)
    with SessionLocal() as db:
        db.query(Event).filter(Event.slug == old_paid).first().expires_at = now - timedelta(days=1)
        db.query(Event).filter(Event.slug == old_unpaid).first().created_at = now - timedelta(days=31)
        db.commit()

    assert client.post("/api/admin/cleanup").status_code == 401
    res = client.post("/api/admin/cleanup", headers={"X-Admin-Token": "test-admin"})
    assert sorted(res.json()["deleted"]) == sorted([old_paid, old_unpaid])
    assert _event(keep_paid) and _event(keep_unpaid)
    assert _event(old_paid) is None and _event(old_unpaid) is None
    assert all(p.startswith("staging/events/") for p in wiped) and len(wiped) == 2
