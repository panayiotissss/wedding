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

import asyncio
import hashlib
import io
import json
import logging
import re
import secrets
import uuid
import zipfile
from contextlib import asynccontextmanager
from datetime import timedelta, timezone
from pathlib import Path

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, field_validator
from sqlalchemy import func
from sqlalchemy.orm import Session
from starlette.middleware.sessions import SessionMiddleware

import config
import emailcheck
import mailer
import payments
import storage
from db import AccessCode, Event, Invitation, PasswordReset, Rsvp, SessionLocal, get_db, init_db, now_utc
from security import hash_password, verify_password

log = logging.getLogger("confetti")


# ---------------------------------------------------------------------------
# App + middleware
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    task = asyncio.create_task(_cleanup_loop()) if config.CLEANUP_INTERVAL_HOURS > 0 else None
    yield
    if task:
        task.cancel()


async def _cleanup_loop():
    """Daily auto-delete (WED-6), run inside the app so no extra cron service
    is needed. The first run waits a minute so startup stays fast."""
    await asyncio.sleep(60)
    while True:
        try:
            deleted = await asyncio.to_thread(_cleanup_once)
            if deleted:
                log.info("Cleanup deleted %d gallery(ies): %s", len(deleted), deleted)
        except Exception:
            log.exception("Cleanup failed")
        await asyncio.sleep(config.CLEANUP_INTERVAL_HOURS * 3600)


def _cleanup_once() -> list[str]:
    db = SessionLocal()
    try:
        deleted = run_cleanup(db)
        freeup = run_freeup(db)
        if any(freeup.values()):
            log.info("Storage free-up: %s", freeup)
        return deleted
    finally:
        db.close()


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
    """Mark an event paid & active, and set its 1-year expiry (WED-6).
    Turns on what the couple bought at signup (photos, invitation or both)."""
    plan = ev.plan or "photos"
    ev.photos = bool(ev.photos) or plan in ("photos", "bundle")
    ev.invite = bool(ev.invite) or plan in ("invite", "bundle")
    ev.status = "active"
    ev.paid_at = now_utc()
    ev.expires_at = ev.paid_at + timedelta(days=config.EVENT_TTL_DAYS)


def _referrer(db: Session, ref: str | None) -> str | None:
    """The referring gallery's slug, only if it really exists."""
    if ref and db.query(Event.id).filter(Event.slug == ref).first():
        return ref
    return None


def _send_welcome(ev: Event) -> None:
    """Welcome email with the gallery link and QR code (runs after the response)."""
    if ev.contact_email:
        base = config.PUBLIC_BASE_URL
        mailer.welcome(ev.contact_email, ev.couple_names,
                       f"{base}/e/{ev.slug}/gallery" if ev.photos else None,
                       f"{base}/e/{ev.slug}/qr.png" if ev.photos else None,
                       f"{base}/e/{ev.slug}/invite" if ev.invite else None)


def check_password_rules(v: str) -> str:
    """Signup and reset share these rules; mirrored in static/create.html."""
    if len(v) < 8:
        raise ValueError("Password must be at least 8 characters.")
    if not re.search(r"[A-Za-z]", v) or not re.search(r"\d", v):
        raise ValueError("Password needs at least one letter and one number.")
    return v


def _unique_slug(db: Session) -> str:
    for _ in range(10):
        slug = uuid.uuid4().hex[:8]
        if not db.query(Event).filter(Event.slug == slug).first():
            return slug
    raise HTTPException(status_code=500, detail="Could not allocate a slug")


# ---------------------------------------------------------------------------
# Page routes (serve static HTML shells; the JS reads the slug from the URL)
# ---------------------------------------------------------------------------

REF_RE = re.compile(r"^[a-z0-9]{4,16}$")


@app.get("/")
def landing_page(request: Request, ref: str | None = None):
    # WED-23: remember which gallery's guest page sent this visitor.
    if ref and REF_RE.match(ref):
        request.session["ref"] = ref
    return FileResponse("static/landing.html")


# For search engines and AI agents: what's public, what the product is, where to start.
_PUBLIC_PAGES = ["/", "/create", "/login", "/terms", "/privacy"]


@app.get("/robots.txt", include_in_schema=False)
def robots_txt():
    base = config.PUBLIC_BASE_URL
    body = ("User-agent: *\nAllow: /\nDisallow: /api/\nDisallow: /e/\nDisallow: /i/\nDisallow: /admin\n"
            f"Sitemap: {base}/sitemap.xml\n")
    return Response(body, media_type="text/plain")


@app.get("/sitemap.xml", include_in_schema=False)
def sitemap_xml():
    base = config.PUBLIC_BASE_URL
    urls = "".join(f"<url><loc>{base}{p}</loc></url>" for p in _PUBLIC_PAGES)
    return Response('<?xml version="1.0" encoding="UTF-8"?>'
                    f'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{urls}</urlset>',
                    media_type="application/xml")


@app.get("/llms.txt", include_in_schema=False)
def llms_txt():
    base = config.PUBLIC_BASE_URL
    body = f"""# Confetti

> Wedding invitations with RSVP, and a QR code that collects every guest's photos into one private gallery. One-time prices, no subscription, no app for guests.

## Products
- Invitation + RSVP, EUR 20: an animated invitation (a film or a scrolling website) sent as one link; guests reply in one tap; the couple sees a live list and can export CSV.
- Photo gallery, EUR 25: one QR code; guests upload full-quality photos and videos from their phone browser; only the couple can view; live for 12 months.
- Both, EUR 39.

## Start
- Sign up: {base}/create?plan=invite | {base}/create?plan=photos | {base}/create?plan=bundle
- Log in: {base}/login
- Terms: {base}/terms
- Privacy: {base}/privacy
- Support: support@confettiwedding.net
"""
    return Response(body, media_type="text/plain")


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    return FileResponse("static/favicon.svg", media_type="image/svg+xml")


@app.get("/create")
def create_page():
    return FileResponse("static/create.html")


@app.get("/create/success")
def success_page():
    return FileResponse("static/success.html")


@app.get("/login")
def login_page():
    return FileResponse("static/login.html")


@app.get("/terms")
def terms_page():
    return FileResponse("static/terms.html")


@app.get("/privacy")
def privacy_page():
    return FileResponse("static/privacy.html")


@app.get("/forgot")
def forgot_page():
    return FileResponse("static/forgot.html")


@app.get("/reset")
def reset_page():
    return FileResponse("static/reset.html")


@app.get("/e/{slug}")
def event_upload_page(slug: str):
    return FileResponse("static/event.html")


@app.get("/e/{slug}/home")
def account_home_page(slug: str):
    return FileResponse("static/home.html")


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


PLANS = {"photos", "invite", "bundle"}


def _home_url(ev: Event) -> str:
    """Where a couple lands after login/payment: the one thing they have, or both."""
    if ev.photos and ev.invite:
        return f"/e/{ev.slug}/home"
    if ev.invite:
        return f"/e/{ev.slug}/invite"
    return f"/e/{ev.slug}/gallery"


class CreateEventRequest(BaseModel):
    couple_names: str
    password: str
    email: str                       # now REQUIRED — it's the login identifier
    access_code: str | None = None   # optional comp code (skips payment)
    plan: str = "photos"             # photos (€25) | invite (€20) | bundle (€39)

    @field_validator("plan")
    @classmethod
    def check_plan(cls, v: str) -> str:
        if v not in PLANS:
            raise ValueError("Choose photos, invitation or both.")
        return v

    @field_validator("couple_names")
    @classmethod
    def check_names(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Couple names are required")
        return v.strip()[:200]

    @field_validator("password")
    @classmethod
    def check_password(cls, v: str) -> str:
        return check_password_rules(v)

    @field_validator("email")
    @classmethod
    def check_email(cls, v: str) -> str:
        return emailcheck.check_email(v)


def _check_access_code(db: Session, code: str) -> tuple[bool, "AccessCode | None"]:
    """Validate an access code: a partner's code from the database (WED-24) or
    the single FREE_ACCESS_CODE from the environment. Raises a clear 400 for a
    used-up code, and for an unknown one when payments are on."""
    if not code:
        return False, None
    partner_code = db.get(AccessCode, code.upper())
    if partner_code and partner_code.active:
        if partner_code.max_uses is not None and partner_code.uses >= partner_code.max_uses:
            raise HTTPException(status_code=400, detail="That access code has already been used up.")
        return True, partner_code
    if config.FREE_ACCESS_CODE and code == config.FREE_ACCESS_CODE:
        return True, None
    if config.PAYMENTS_ENABLED:
        # Say so, rather than silently sending them to checkout.
        raise HTTPException(status_code=400, detail="That access code isn't valid.")
    return False, None


@app.post("/api/events")
def create_event(body: CreateEventRequest, request: Request, background: BackgroundTasks,
                 db: Session = Depends(get_db)):
    email = _normalize_email(body.email)

    # A valid code skips payment even when Stripe is on: a partner's code from
    # the database (WED-24) or the single FREE_ACCESS_CODE from the environment.
    code = (body.access_code or "").strip()
    code_ok, partner_code = _check_access_code(db, code)

    # Email is the login identifier, so it must be unique. The exception is an
    # unpaid signup with the same email: someone who went to checkout and came
    # back (e.g. to use an access code instead). Reuse that row rather than
    # blocking them; keeping its id means an open checkout still matches it.
    ev = db.query(Event).filter(func.lower(Event.contact_email) == email).first()
    if ev and ev.status != "pending_payment":
        raise HTTPException(
            status_code=409,
            detail="An account with this email already exists. Please log in instead.",
        )
    if ev:
        ev.couple_names = body.couple_names
        ev.password_hash = hash_password(body.password)
        ev.plan = body.plan
    else:
        slug = _unique_slug(db)
        ev = Event(
            slug=slug,
            couple_names=body.couple_names,
            password_hash=hash_password(body.password),
            contact_email=email,
            status="pending_payment",
            plan=body.plan,
            # Readable R2 folder: couple name + slug (slug guarantees uniqueness,
            # since two different couples can share the same names).
            storage_prefix=f"{slugify_names(body.couple_names)}-{slug}",
            referred_by=_referrer(db, request.session.get("ref")),
        )

    if config.PAYMENTS_ENABLED and not code_ok:
        if not payments.link_for(body.plan):
            raise HTTPException(status_code=503, detail="This option opens very soon. Use an access code for now.")
        db.add(ev)
        db.commit()
        _grant_auth(request, ev.slug)  # so the couple lands in their gallery after paying
        request.session["last_slug"] = ev.slug  # so /create/success works after the Stripe round-trip
        request.session.pop("pending_addon", None)
        return {"slug": ev.slug, "status": ev.status, "payment_url": payments.payment_url(ev)}

    # Free path: stub mode (no Stripe) OR a valid comp code — unlock immediately.
    _activate(ev)
    if code_ok:
        ev.access_code = partner_code.code if partner_code else "FREE_ACCESS_CODE"
        if partner_code:
            partner_code.uses += 1
    db.add(ev)
    db.commit()
    background.add_task(_send_welcome, ev)
    _grant_auth(request, ev.slug)
    request.session["last_slug"] = ev.slug
    request.session.pop("pending_addon", None)
    return {"slug": ev.slug, "status": ev.status, "payment_url": f"/create/success?slug={ev.slug}",
            "next": _home_url(ev)}


class EmailLoginRequest(BaseModel):
    email: str
    password: str


@app.post("/api/login")
def email_login(body: EmailLoginRequest, request: Request, db: Session = Depends(get_db)):
    """Couple login by the email + password collected at signup — so they can get
    back into their gallery from any device without needing the original link."""
    email = _normalize_email(body.email)
    # lower() on both sides: rows created before emails were normalized can
    # still hold mixed case.
    ev = db.query(Event).filter(func.lower(Event.contact_email) == email).first()
    if not ev or not verify_password(body.password, ev.password_hash):
        raise HTTPException(status_code=401, detail="Wrong email or password")
    _grant_auth(request, ev.slug)
    request.session["last_slug"] = ev.slug
    return {"slug": ev.slug, "next": _home_url(ev) if ev.status == "active" else f"/e/{ev.slug}/gallery"}


# ---------------------------------------------------------------------------
# Forgot password (WED-21): emailed one-time link, valid for 1 hour
# ---------------------------------------------------------------------------

RESET_TTL = timedelta(hours=1)
RESET_COOLDOWN = timedelta(seconds=60)


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _aware(dt):
    # SQLite hands back naive datetimes; Postgres keeps the timezone.
    return dt if dt is None or dt.tzinfo else dt.replace(tzinfo=timezone.utc)


class ForgotRequest(BaseModel):
    email: str


@app.post("/api/password/forgot")
def forgot_password(body: ForgotRequest, background: BackgroundTasks, db: Session = Depends(get_db)):
    """Always answers the same way, so it can't be used to find out who has a gallery."""
    email = _normalize_email(body.email)
    ev = db.query(Event).filter(func.lower(Event.contact_email) == email).first()
    if ev:
        latest = (db.query(PasswordReset).filter(PasswordReset.event_id == ev.id)
                  .order_by(PasswordReset.created_at.desc()).first())
        # One email a minute per gallery, so the form can't be used to spam someone.
        if not latest or now_utc() - _aware(latest.created_at) > RESET_COOLDOWN:
            token = secrets.token_urlsafe(32)
            db.add(PasswordReset(token_hash=_hash_token(token), event_id=ev.id,
                                 expires_at=now_utc() + RESET_TTL))
            db.commit()
            background.add_task(mailer.password_reset, ev.contact_email,
                                f"{config.PUBLIC_BASE_URL}/reset?token={token}")
    return {"ok": True}


class ResetRequest(BaseModel):
    token: str
    password: str

    @field_validator("password")
    @classmethod
    def check_password(cls, v: str) -> str:
        return check_password_rules(v)


@app.post("/api/password/reset")
def reset_password(body: ResetRequest, request: Request, db: Session = Depends(get_db)):
    pr = db.query(PasswordReset).filter(PasswordReset.token_hash == _hash_token(body.token)).first()
    if not pr or pr.used_at or _aware(pr.expires_at) < now_utc():
        raise HTTPException(status_code=400,
                            detail="This reset link has expired or was already used. Please request a new one.")
    ev = db.query(Event).filter(Event.id == pr.event_id).first()
    if not ev:
        raise HTTPException(status_code=400, detail="This reset link is no longer valid.")

    ev.password_hash = hash_password(body.password)
    # Burn this link and any other open links for the same gallery.
    for other in db.query(PasswordReset).filter(PasswordReset.event_id == ev.id,
                                                 PasswordReset.used_at.is_(None)):
        other.used_at = now_utc()
    db.commit()
    _grant_auth(request, ev.slug)
    request.session["last_slug"] = ev.slug
    return {"slug": ev.slug}


@app.get("/api/my-latest")
def my_latest(request: Request):
    """The gallery this browser session most recently created — lets the
    Stripe success page find it without a slug in the (static) return URL."""
    return {"slug": request.session.get("last_slug"), "addon": request.session.get("pending_addon")}


@app.get("/api/events/{slug}/status")
def event_status(slug: str, db: Session = Depends(get_db)):
    ev = get_event_or_404(db, slug)
    return {"slug": ev.slug, "status": ev.status, "names": ev.couple_names,
            "photos": bool(ev.photos), "invite": bool(ev.invite), "next": _home_url(ev)}


@app.post("/api/stripe/webhook")
async def stripe_webhook(request: Request, background: BackgroundTasks,
                         db: Session = Depends(get_db)):
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
        addon = None
        for prefix, product in ((payments.INVITE_REF_PREFIX, "invite"), (payments.PHOTOS_REF_PREFIX, "photos")):
            if ref and ref.startswith(prefix):
                addon, ref = product, ref[len(prefix):]
        if addon:
            # An add-on bought later from inside the account.
            ev = db.query(Event).filter(Event.id == ref).first()
            if ev:
                setattr(ev, addon, True)
                if ev.status != "active":
                    _activate(ev)
                db.commit()
        elif ref:
            ev = db.query(Event).filter(Event.id == ref).first()
            if ev and ev.status != "active":
                _activate(ev)
                ev.stripe_ref = session.get("id")
                db.commit()
                background.add_task(_send_welcome, ev)

    return {"received": True}


# ---------------------------------------------------------------------------
# Per-event public info (WED-4 dynamic name + paywall state)
# ---------------------------------------------------------------------------

@app.get("/api/e/{slug}/info")
def event_info(slug: str, db: Session = Depends(get_db)):
    ev = get_event_or_404(db, slug)
    return {"slug": ev.slug, "names": ev.couple_names, "active": ev.status == "active" and bool(ev.photos)}


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
    if ev.status != "active" or not ev.photos:
        raise HTTPException(status_code=403, detail="This gallery isn't active yet.")

    if ev.freeup_warned_at or ev.files_freed_at:
        # New photos arriving means the gallery is in use again (WED-34).
        ev.freeup_warned_at = ev.files_freed_at = None
        db.commit()
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


@app.get("/api/e/{slug}/account")
def account(slug: str, request: Request, db: Session = Depends(get_db)):
    """What this couple has, for the home page and the links between pages."""
    require_event_auth(request, slug)
    ev = get_event_or_404(db, slug)
    return {"slug": ev.slug, "names": ev.couple_names, "active": ev.status == "active",
            "photos": bool(ev.photos), "invite": bool(ev.invite), "home": _home_url(ev),
            "prices": {"photos": 25, "invite": 20}}


class AddonRequest(BaseModel):
    product: str
    access_code: str | None = None

    @field_validator("product")
    @classmethod
    def check_product(cls, v: str) -> str:
        if v not in ("photos", "invite"):
            raise ValueError("Unknown add-on.")
        return v


@app.post("/api/e/{slug}/addon")
def buy_addon(slug: str, body: AddonRequest, request: Request, db: Session = Depends(get_db)):
    """Add the photo gallery or the invitation to an existing account:
    free with a valid code (or in stub mode), otherwise Stripe checkout."""
    require_event_auth(request, slug)
    ev = get_event_or_404(db, slug)
    if getattr(ev, body.product):
        return {"ok": True, "next": _home_url(ev)}
    code_ok, partner_code = _check_access_code(db, (body.access_code or "").strip())
    if code_ok or not config.PAYMENTS_ENABLED:
        setattr(ev, body.product, True)
        if ev.status != "active":
            _activate(ev)
        if partner_code:
            partner_code.uses += 1
        db.commit()
        return {"ok": True, "next": _home_url(ev)}
    if not payments.link_for(body.product):
        raise HTTPException(status_code=503, detail="This add-on opens very soon. Use an access code for now.")
    prefix = payments.INVITE_REF_PREFIX if body.product == "invite" else payments.PHOTOS_REF_PREFIX
    request.session["last_slug"] = ev.slug
    request.session["pending_addon"] = body.product  # the success page waits for it
    return {"ok": False, "payment_url": payments.payment_url(ev, body.product, prefix)}


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
    ev.last_download_at = now_utc()   # WED-34
    db.commit()

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


@app.post("/api/e/{slug}/delete-all")
def delete_all_photos(slug: str, request: Request, db: Session = Depends(get_db)):
    """Wipe this event's photos (and its cached zip). Scoped to this event's
    prefixes only, so no other couple's files can be touched."""
    ev = get_event_or_404(db, slug)
    require_event_auth(request, slug)
    key = _storage_key(ev)
    deleted = storage.delete_prefix(storage.photos_prefix(key))
    storage.delete_prefix(storage.exports_prefix(key))
    ev.all_deleted_at = now_utc()   # WED-34
    ev.freeup_warned_at = None
    db.commit()
    return {"ok": True, "deleted": deleted}


@app.get("/api/e/{slug}/storage")
def storage_status(slug: str, request: Request, db: Session = Depends(get_db)):
    """What the gallery page needs to show the free-up banner (couple only)."""
    ev = get_event_or_404(db, slug)
    require_event_auth(request, slug)
    clear_on = None
    if ev.freeup_warned_at:
        clear_on = (_aware(ev.freeup_warned_at) + FREEUP_GRACE).date().isoformat()
    return {"downloaded": ev.last_download_at is not None, "clear_on": clear_on}


@app.post("/api/e/{slug}/keep")
def keep_photos(slug: str, request: Request, db: Session = Depends(get_db)):
    """Couple chose to keep their photos online: cancel the free-up and restart the clock."""
    ev = get_event_or_404(db, slug)
    require_event_auth(request, slug)
    ev.freeup_warned_at = None
    ev.last_download_at = now_utc()
    db.commit()
    return {"ok": True}


# ---------------------------------------------------------------------------
# Admin — auto-delete expired events (WED-6)
# ---------------------------------------------------------------------------

UNPAID_TTL = timedelta(days=30)


def run_cleanup(db: Session) -> list[str]:
    """Delete galleries past their 12-month expiry and sign-ups never paid
    within 30 days, with all their R2 files and reset links. Both promises are
    in the Privacy Policy (static/privacy.html)."""
    now = now_utc()
    expired = db.query(Event).filter(Event.expires_at.isnot(None), Event.expires_at < now).all()
    unpaid = (db.query(Event)
              .filter(Event.status == "pending_payment", Event.created_at < now - UNPAID_TTL)
              .all())

    deleted: list[str] = []
    for ev in expired + unpaid:
        storage.delete_prefix(storage.event_root(_storage_key(ev)))
        db.query(PasswordReset).filter(PasswordReset.event_id == ev.id).delete()
        db.delete(ev)
        deleted.append(ev.slug)
    db.commit()
    return deleted


# WED-34: free up storage for galleries the couple has already taken home.
ZIP_CACHE_TTL = timedelta(days=3)     # the cached "Download all" zip doubles storage
FREEUP_AFTER = timedelta(days=60)     # downloaded + no new uploads for this long -> warn
FREEUP_GRACE = timedelta(days=14)     # then clear the photos this long after the warning


def run_freeup(db: Session) -> dict:
    """Daily storage housekeeping; the couple is always warned before photos go."""
    now = now_utc()
    zips_cleared, warned, freed = [], [], []
    candidates = (db.query(Event)
                  .filter(Event.status == "active", Event.last_download_at.isnot(None), Event.files_freed_at.is_(None))
                  .all())
    for ev in candidates:
        key = _storage_key(ev)
        downloaded = _aware(ev.last_download_at)
        if now - downloaded > ZIP_CACHE_TTL and storage.delete_prefix(storage.exports_prefix(key)):
            zips_cleared.append(ev.slug)
        photos = storage.list_prefix(storage.photos_prefix(key))
        if not photos:
            continue
        newest = max(_aware(o["LastModified"]) for o in photos)
        if newest > downloaded:
            continue   # new photos since the download: the couple hasn't got everything
        if now - downloaded < FREEUP_AFTER:
            continue
        if not ev.freeup_warned_at:
            ev.freeup_warned_at = now
            if ev.contact_email:
                mailer.freeup_warning(ev.contact_email, ev.couple_names,
                                      f"{config.PUBLIC_BASE_URL}/e/{ev.slug}/gallery",
                                      (now + FREEUP_GRACE).strftime("%-d %B %Y"))
            warned.append(ev.slug)
        elif now - _aware(ev.freeup_warned_at) >= FREEUP_GRACE:
            storage.delete_prefix(storage.photos_prefix(key))
            storage.delete_prefix(storage.exports_prefix(key))
            ev.files_freed_at = now
            freed.append(ev.slug)
    db.commit()
    return {"zips_cleared": zips_cleared, "warned": warned, "freed": freed}


def _require_admin(request: Request) -> None:
    if not config.ADMIN_TOKEN or request.headers.get("x-admin-token") != config.ADMIN_TOKEN:
        raise HTTPException(status_code=401, detail="Unauthorized")


@app.post("/api/admin/cleanup")
def cleanup(request: Request, db: Session = Depends(get_db)):
    """Run the cleanup now. It also runs daily on its own (see _cleanup_loop).
    Protected by ADMIN_TOKEN:  curl -X POST -H 'X-Admin-Token: ...' /api/admin/cleanup"""
    _require_admin(request)
    deleted = run_cleanup(db)
    return {"deleted": deleted, "count": len(deleted), "freeup": run_freeup(db)}


@app.get("/api/admin/report")
def admin_report(request: Request, db: Session = Depends(get_db)):
    """Every gallery with its storage use and housekeeping dates (WED-34)."""
    _require_admin(request)
    iso = lambda d: _aware(d).isoformat() if d else None
    rows, total_bytes = [], 0
    for ev in db.query(Event).order_by(Event.created_at.desc()).all():
        objs = storage.list_prefix(storage.event_root(_storage_key(ev)))
        photos = [o for o in objs if "/photos/" in o["Key"]]
        size = sum(o.get("Size", 0) for o in objs)
        total_bytes += size
        rows.append({
            "slug": ev.slug, "names": ev.couple_names, "email": ev.contact_email, "status": ev.status,
            "created_at": iso(ev.created_at), "paid_at": iso(ev.paid_at), "expires_at": iso(ev.expires_at),
            "referred_by": ev.referred_by, "access_code": ev.access_code, "photos": len(photos), "bytes": size,
            "last_upload": iso(max((o["LastModified"] for o in photos), default=None)),
            "last_download_at": iso(ev.last_download_at), "all_deleted_at": iso(ev.all_deleted_at),
            "freeup_warned_at": iso(ev.freeup_warned_at), "files_freed_at": iso(ev.files_freed_at),
        })
    return {
        "totals": {
            "galleries": len(rows), "active": sum(r["status"] == "active" for r in rows),
            "paid": sum(1 for r in rows if r["paid_at"]), "from_guests": sum(1 for r in rows if r["referred_by"]),
            "photos": sum(r["photos"] for r in rows), "bytes": total_bytes,
            "gb": round(total_bytes / 1e9, 3),
        },
        "galleries": rows,
    }


class CodeRequest(BaseModel):
    code: str
    partner: str
    max_uses: int | None = 2

    @field_validator("code")
    @classmethod
    def check_code(cls, v: str) -> str:
        v = v.strip().upper()
        if not re.fullmatch(r"[A-Z0-9-]{4,40}", v):
            raise ValueError("Codes are 4–40 letters, numbers or dashes.")
        return v


@app.get("/api/admin/codes")
def list_codes(request: Request, db: Session = Depends(get_db)):
    _require_admin(request)
    counts = dict(db.query(Event.access_code, func.count(Event.id)).group_by(Event.access_code).all())
    return [{"code": c.code, "partner": c.partner, "max_uses": c.max_uses, "uses": c.uses, "active": c.active,
             "galleries": counts.get(c.code, 0), "created_at": _aware(c.created_at).isoformat()}
            for c in db.query(AccessCode).order_by(AccessCode.created_at.desc()).all()]


@app.post("/api/admin/codes")
def create_code(body: CodeRequest, request: Request, db: Session = Depends(get_db)):
    _require_admin(request)
    if db.get(AccessCode, body.code):
        raise HTTPException(status_code=409, detail="That code already exists.")
    db.add(AccessCode(code=body.code, partner=body.partner.strip()[:120], max_uses=body.max_uses))
    db.commit()
    return {"ok": True, "code": body.code}


@app.post("/api/admin/codes/{code}/toggle")
def toggle_code(code: str, request: Request, db: Session = Depends(get_db)):
    _require_admin(request)
    c = db.get(AccessCode, code.upper())
    if not c:
        raise HTTPException(status_code=404, detail="No such code.")
    c.active = not c.active
    db.commit()
    return {"ok": True, "active": c.active}


@app.get("/admin")
def admin_page():
    return FileResponse("static/admin.html")


# ---------------------------------------------------------------------------
# WED-36: animated invitation + RSVP
# ---------------------------------------------------------------------------
# One invitation per couple: a scrolling wedding website in one of five
# designs, made of the couple's own sections, ending in the RSVP. Guests open
# /i/<slug>. Any couple can design and preview it; the guest link works once
# the account has the invitation add-on.

# The website designs (static/site.html renders them, static/invite/designs.js
# holds their starter sections). The earlier film invitations were retired.
SITE_THEMES = {"linen", "garden", "noir", "riviera", "bloom"}
MAX_SECTIONS = 30
MAX_INVITE_PHOTO = 15 * 1024 * 1024
INVITE_PHOTO_TYPES = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}
# Fields each website section may have, with their max lengths.
SECTION_FIELDS = {
    "text":      {"title": 80, "body": 1500},
    "event":     {"title": 80, "time": 5, "place": 120, "map_url": 500},
    "photo":     {"key": 300, "caption": 120},
    "countdown": {"title": 80},
    "qa":        {"question": 160, "answer": 800},
}
INVITE_FONTS = {"classic", "romantic", "calligraphy", "modern", "timeless"}
MAX_RSVPS = 1000
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_TIME_RE = re.compile(r"^\d{2}:\d{2}$")
_INVITE_FIELDS = ("names", "date", "time", "venue", "map_url", "reception", "reception_map",
                  "extra1_label", "extra1_text", "extra2_label", "extra2_text",
                  "message", "rsvp_by", "theme", "font", "kind")


def _invite_photo_prefix(ev: Event) -> str:
    return storage.event_root(_storage_key(ev)) + "invite/"


def _load_sections(inv: Invitation | None) -> list:
    try:
        data = json.loads(inv.sections or "[]") if inv else []
        return data if isinstance(data, list) else []
    except ValueError:
        return []


def _clean_sections(raw: list, photo_prefix: str) -> list:
    """Keep only known section types and fields, trimmed to their limits."""
    out = []
    for item in raw[:MAX_SECTIONS]:
        if not isinstance(item, dict) or item.get("type") not in SECTION_FIELDS:
            continue
        sec = {"id": re.sub(r"[^A-Za-z0-9_-]", "", str(item.get("id", "")))[:16] or secrets.token_hex(4),
               "type": item["type"]}
        for field, limit in SECTION_FIELDS[item["type"]].items():
            value = str(item.get(field) or "").strip()[:limit]
            if field == "map_url" and value:
                value = _url(value)
            if field == "time" and value and not _TIME_RE.match(value):
                raise ValueError("Use a time like 18:30.")
            if field == "key" and value and not value.startswith(photo_prefix):
                value = ""  # only photos uploaded to this couple's own folder
            sec[field] = value
        out.append(sec)
    return out


def _get_invitation(db: Session, ev: Event) -> Invitation | None:
    return db.get(Invitation, ev.id)


def _invitation_live(ev: Event | None, inv: Invitation | None) -> bool:
    return bool(ev and inv and ev.status == "active" and ev.invite)


def _invitation_json(ev: Event, inv: Invitation | None) -> dict:
    """The invitation's fields; defaults (names from the account) when there's none yet."""
    data = {f: (getattr(inv, f) or "") if inv else "" for f in _INVITE_FIELDS}
    data["names"] = data["names"] or ev.couple_names or ""
    data["kind"] = "site"
    data["theme"] = data["theme"] if data["theme"] in SITE_THEMES else "linen"
    data["font"] = data["font"] or "classic"
    data["sections"] = _load_sections(inv)
    return data


def _with_photo_urls(data: dict) -> dict:
    """Photo sections get a temporary link the browser can load."""
    for sec in data["sections"]:
        if sec.get("type") == "photo" and sec.get("key"):
            sec["url"] = storage.presign_get(sec["key"], expires=6 * 3600)
    return data


def _clean(v: str, n: int) -> str:
    return " ".join((v or "").split())[:n]


def _url(v: str) -> str:
    v = (v or "").strip()
    if v and not v.startswith(("https://", "http://")):
        raise ValueError("Map links should start with https://")
    return v[:500]


class InvitationRequest(BaseModel):
    names: str
    date: str = ""
    time: str = ""
    venue: str = ""            # ceremony
    map_url: str = ""          # ceremony map
    reception: str = ""
    reception_map: str = ""
    extra1_label: str = ""
    extra1_text: str = ""
    extra2_label: str = ""
    extra2_text: str = ""
    message: str = ""
    rsvp_by: str = ""
    theme: str = "linen"
    font: str = "classic"
    kind: str = "site"
    sections: list = []

    @field_validator("names")
    @classmethod
    def v_names(cls, v: str) -> str:
        v = _clean(v, 120)
        if not v:
            raise ValueError("Add your names.")
        return v

    @field_validator("date", "rsvp_by")
    @classmethod
    def v_date(cls, v: str) -> str:
        v = v.strip()
        if v and not _DATE_RE.match(v):
            raise ValueError("Use a date like 2027-06-12.")
        return v

    @field_validator("time")
    @classmethod
    def v_time(cls, v: str) -> str:
        v = v.strip()
        if v and not _TIME_RE.match(v):
            raise ValueError("Use a time like 18:30.")
        return v

    @field_validator("map_url", "reception_map")
    @classmethod
    def v_map(cls, v: str) -> str:
        return _url(v)

    @field_validator("venue", "reception", "extra1_text", "extra2_text")
    @classmethod
    def v_line(cls, v: str) -> str:
        return _clean(v, 120)

    @field_validator("extra1_label", "extra2_label")
    @classmethod
    def v_label(cls, v: str) -> str:
        return _clean(v, 40)

    @field_validator("message")
    @classmethod
    def v_message(cls, v: str) -> str:
        return v.strip()[:600]

    @field_validator("kind")
    @classmethod
    def v_kind(cls, v: str) -> str:
        return "site"

    @field_validator("theme")
    @classmethod
    def v_theme(cls, v: str) -> str:
        return v if v in SITE_THEMES else "linen"

    @field_validator("font")
    @classmethod
    def v_font(cls, v: str) -> str:
        return v if v in INVITE_FONTS else "classic"


@app.get("/e/{slug}/invite")
def invitation_editor_page(slug: str):
    return FileResponse("static/invite-edit.html")


_SITE_HTML = Path("static/site.html")


def _site_page(title: str, desc: str, theme: str) -> Response:
    from html import escape
    image = f"{config.PUBLIC_BASE_URL}/static/invite/og-{theme if theme in SITE_THEMES else 'linen'}.jpg"
    page = (_SITE_HTML.read_text(encoding="utf-8")
            .replace("__OG_TITLE__", escape(title))
            .replace("__OG_DESC__", escape(desc))
            .replace("__OG_IMAGE__", escape(image)))
    return Response(page, media_type="text/html")


@app.get("/demo/{design}")
def design_demo(design: str):
    """A sample couple in any design: for the landing page and for couples choosing."""
    return _site_page("Confetti · Wedding website invitation", "A sample invitation made with Confetti.", design)


@app.get("/i/{slug}")
def invitation_guest_page(slug: str, db: Session = Depends(get_db)):
    """The guest page, with the couple's names in the link preview (WhatsApp, iMessage)."""
    ev = db.query(Event).filter(Event.slug == slug).first()
    inv = _get_invitation(db, ev) if ev else None
    if _invitation_live(ev, inv):
        return _site_page(f"{inv.names or ev.couple_names} · You're invited",
                          "Tap to open your invitation and reply.", inv.theme)
    return _site_page("You're invited", "A wedding invitation made with Confetti.", inv.theme if inv else "linen")


@app.get("/api/e/{slug}/invitation")
def get_invitation(slug: str, request: Request, db: Session = Depends(get_db)):
    require_event_auth(request, slug)
    ev = get_event_or_404(db, slug)
    inv = _get_invitation(db, ev)
    data = _with_photo_urls(_invitation_json(ev, inv))
    data["saved"] = inv is not None
    data["live"] = _invitation_live(ev, inv)
    data["has_invite"] = bool(ev.invite) and ev.status == "active"
    data["has_photos"] = bool(ev.photos) and ev.status == "active"
    data["guest_url"] = f"{config.PUBLIC_BASE_URL}/i/{ev.slug}"
    return data


@app.put("/api/e/{slug}/invitation")
def save_invitation(slug: str, body: InvitationRequest, request: Request, db: Session = Depends(get_db)):
    require_event_auth(request, slug)
    ev = get_event_or_404(db, slug)
    inv = _get_invitation(db, ev)
    if not inv:
        inv = Invitation(event_id=ev.id)
        db.add(inv)
    data = body.model_dump()
    try:
        sections = _clean_sections(data.pop("sections"), _invite_photo_prefix(ev))
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    for field, value in data.items():
        setattr(inv, field, value)
    inv.sections = json.dumps(sections)
    inv.updated_at = now_utc()
    db.commit()
    data = _with_photo_urls(_invitation_json(ev, inv))
    data["live"] = _invitation_live(ev, inv)
    return data


class InvitePhotoRequest(BaseModel):
    content_type: str
    file_size: int


@app.post("/api/e/{slug}/invitation/photo-url")
def invitation_photo_url(slug: str, body: InvitePhotoRequest, request: Request, db: Session = Depends(get_db)):
    """Signed upload link for a photo on the couple's website invitation."""
    require_event_auth(request, slug)
    ev = get_event_or_404(db, slug)
    ext = INVITE_PHOTO_TYPES.get(body.content_type)
    if not ext:
        raise HTTPException(status_code=400, detail="Use a JPG, PNG or WebP photo.")
    if not 0 < body.file_size <= MAX_INVITE_PHOTO:
        raise HTTPException(status_code=400, detail="That photo is too large (15 MB max).")
    key = f"{_invite_photo_prefix(ev)}{uuid.uuid4().hex}{ext}"
    return {"url": storage.presign_put(key, body.content_type), "key": key,
            "preview_url": storage.presign_get(key, expires=6 * 3600)}


@app.get("/e/{slug}/invite-qr.png")
def invitation_qr(slug: str, db: Session = Depends(get_db)):
    """QR code for printed invitations or save-the-dates."""
    ev = get_event_or_404(db, slug)
    import qrcode
    img = qrcode.make(f"{config.PUBLIC_BASE_URL}/i/{ev.slug}", box_size=10, border=2)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return Response(buf.getvalue(), media_type="image/png")


@app.get("/api/i/{slug}")
def public_invitation(slug: str, request: Request, db: Session = Depends(get_db)):
    """What a guest sees. The couple can always see theirs (preview)."""
    ev = db.query(Event).filter(Event.slug == slug).first()
    inv = _get_invitation(db, ev) if ev else None
    live = _invitation_live(ev, inv)
    is_couple = bool(ev) and slug in request.session.get("auth_events", [])
    if not inv or not (live or is_couple):
        raise HTTPException(status_code=404, detail="Invitation not found")
    data = _with_photo_urls(_invitation_json(ev, inv))
    data["preview"] = not live
    data["upload_url"] = f"/e/{ev.slug}" if ev.status == "active" and ev.photos else None
    return data


class RsvpRequest(BaseModel):
    name: str
    attending: bool
    guests: int = 1
    note: str = ""

    @field_validator("name")
    @classmethod
    def v_name(cls, v: str) -> str:
        v = " ".join(v.split())
        if not v:
            raise ValueError("Add your name.")
        return v[:120]

    @field_validator("guests")
    @classmethod
    def v_guests(cls, v: int) -> int:
        if v < 1 or v > 20:
            raise ValueError("Number of people should be between 1 and 20.")
        return v

    @field_validator("note")
    @classmethod
    def v_note(cls, v: str) -> str:
        return v.strip()[:600]


@app.post("/api/i/{slug}/rsvp")
def submit_rsvp(slug: str, body: RsvpRequest, db: Session = Depends(get_db)):
    ev = db.query(Event).filter(Event.slug == slug).first()
    inv = _get_invitation(db, ev) if ev else None
    if not _invitation_live(ev, inv):
        raise HTTPException(status_code=404, detail="Invitation not found")
    # Same name again = a changed answer, not a second guest.
    existing = (db.query(Rsvp).filter(Rsvp.event_id == ev.id, func.lower(Rsvp.name) == body.name.lower())
                .first())
    if existing:
        rsvp = existing
    else:
        if db.query(func.count(Rsvp.id)).filter(Rsvp.event_id == ev.id).scalar() >= MAX_RSVPS:
            raise HTTPException(status_code=429, detail="This invitation can't take more replies.")
        rsvp = Rsvp(event_id=ev.id, name=body.name)
        db.add(rsvp)
    rsvp.attending = body.attending
    rsvp.guests = body.guests if body.attending else 0
    rsvp.note = body.note
    rsvp.updated_at = now_utc()
    db.commit()
    return {"ok": True, "updated": existing is not None}


def _rsvp_rows(db: Session, ev: Event) -> list[Rsvp]:
    return db.query(Rsvp).filter(Rsvp.event_id == ev.id).order_by(Rsvp.updated_at.desc()).all()


@app.get("/api/e/{slug}/rsvps")
def list_rsvps(slug: str, request: Request, db: Session = Depends(get_db)):
    require_event_auth(request, slug)
    ev = get_event_or_404(db, slug)
    rows = _rsvp_rows(db, ev)
    yes = [r for r in rows if r.attending]
    return {
        "totals": {"replies": len(rows), "yes": len(yes), "no": len(rows) - len(yes),
                   "people": sum(r.guests for r in yes)},
        "rsvps": [{"id": r.id, "name": r.name, "attending": r.attending, "guests": r.guests,
                   "note": r.note, "at": r.updated_at.isoformat()} for r in rows],
    }


def _csv_safe(v) -> str:
    """Stop spreadsheet apps treating a guest's text as a formula."""
    v = str(v)
    return "'" + v if v[:1] in ("=", "+", "-", "@", "\t", "\r") else v


@app.get("/api/e/{slug}/rsvps.csv")
def export_rsvps(slug: str, request: Request, db: Session = Depends(get_db)):
    require_event_auth(request, slug)
    ev = get_event_or_404(db, slug)
    import csv
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Name", "Coming", "People", "Note", "Replied"])
    for r in _rsvp_rows(db, ev):
        w.writerow([_csv_safe(r.name), "Yes" if r.attending else "No", r.guests,
                    _csv_safe(r.note), r.updated_at.strftime("%Y-%m-%d %H:%M")])
    return Response(buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": 'attachment; filename="rsvps.csv"'})


# ---------------------------------------------------------------------------
# Static assets
# ---------------------------------------------------------------------------

app.mount("/static", StaticFiles(directory="static"), name="static")
