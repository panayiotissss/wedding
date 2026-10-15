"""
Central configuration.  Everything secret comes from env vars.

Required in every environment:
    R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, R2_BUCKET, R2_ENDPOINT
    SESSION_SECRET
    DATABASE_URL           (Postgres — Neon/Render free tier)

Optional:
    PUBLIC_BASE_URL        (e.g. https://yourapp.com — used for QR codes / links)
    COOKIE_SECURE          ("false" only for local http://localhost testing)
    EVENT_TTL_DAYS         (default 365 — auto-delete window)
    ADMIN_TOKEN            (protects the cleanup endpoint)
    R2_KEY_ROOT            (default "events"; staging uses "staging/events" so
                            it can share the production bucket without overlap)

Stripe (optional — if BOTH are unset the app runs in STUB mode where events
unlock instantly without payment, so you can demo the flow end-to-end):
    STRIPE_PAYMENT_LINK_URL   (a Payment Link you created in the Stripe dashboard)
    STRIPE_WEBHOOK_SECRET     (signing secret for the webhook endpoint)
"""

import os

from dotenv import load_dotenv

load_dotenv()


def _require(name: str) -> str:
    v = os.environ.get(name)
    if not v:
        raise RuntimeError(f"Missing required env var: {name}")
    return v


def _optional(name: str, default: str | None = None) -> str | None:
    v = os.environ.get(name)
    return v if v not in (None, "") else default


# ── R2 / object storage ────────────────────────────────────────────────────
R2_ACCESS_KEY_ID     = _require("R2_ACCESS_KEY_ID")
R2_SECRET_ACCESS_KEY = _require("R2_SECRET_ACCESS_KEY")
R2_BUCKET            = _require("R2_BUCKET")
R2_ENDPOINT          = _require("R2_ENDPOINT")
# Top-level folder for all event media in the bucket.
R2_KEY_ROOT          = (_optional("R2_KEY_ROOT", "events") or "events").strip("/")

# ── App / sessions / db ────────────────────────────────────────────────────
SESSION_SECRET  = _require("SESSION_SECRET")
DATABASE_URL    = _require("DATABASE_URL")
PUBLIC_BASE_URL = (_optional("PUBLIC_BASE_URL", "http://localhost:8000") or "").rstrip("/")
COOKIE_SECURE   = (_optional("COOKIE_SECURE", "true") or "true").lower() != "false"

# ── Business rules ─────────────────────────────────────────────────────────
EVENT_TTL_DAYS = int(_optional("EVENT_TTL_DAYS", "365") or "365")
ADMIN_TOKEN    = _optional("ADMIN_TOKEN")

MAX_FILE_SIZE = 500 * 1024 * 1024  # 500 MB — fits longer phone videos (single PUT).
                                   # Raise further only once multipart upload is in place.

ALLOWED_TYPES = {
    "image/jpeg",
    "image/png",
    "image/gif",
    "image/webp",
    "image/heic",
    "image/heif",
    "video/mp4",
    "video/quicktime",
    "video/x-msvideo",
}

# ── Stripe (optional) ──────────────────────────────────────────────────────
STRIPE_PAYMENT_LINK_URL = _optional("STRIPE_PAYMENT_LINK_URL")
STRIPE_WEBHOOK_SECRET   = _optional("STRIPE_WEBHOOK_SECRET")

# When Stripe isn't configured we fall back to stub mode: events activate
# immediately on creation so the whole product is demoable without a Stripe
# account.  Flip to real payments simply by setting the two vars above.
PAYMENTS_ENABLED = bool(STRIPE_PAYMENT_LINK_URL and STRIPE_WEBHOOK_SECRET)

# ── Email (Resend) ─────────────────────────────────────────────────────────
# Without RESEND_API_KEY emails are only logged (local dev, tests).
RESEND_API_KEY = _optional("RESEND_API_KEY")
EMAIL_FROM     = _optional("EMAIL_FROM", "Confetti <hello@confettiwedding.net>")
EMAIL_REPLY_TO = _optional("EMAIL_REPLY_TO", "support@confettiwedding.net")

# Reject signup emails whose domain doesn't exist (typos like "gmai1l.com").
# Fails open if DNS is unreachable. Tests turn it off to stay offline.
EMAIL_DNS_CHECK = (_optional("EMAIL_DNS_CHECK", "true") or "true").lower() != "false"

# Optional comp code: when payments are ON, entering this code on /create skips
# payment and unlocks the gallery for free (for friends, giveaways, etc.).
# Leave unset to disable. Ignored in stub mode (everything is already free).
FREE_ACCESS_CODE = _optional("FREE_ACCESS_CODE")
