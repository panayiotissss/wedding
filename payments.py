"""
Stripe payments (WED-7) — "Payment Link" approach.

Flow:
  1. Couple creates an event  → we build the Payment Link URL with
     client_reference_id=<event.id> appended.
  2. Browser is redirected to Stripe's hosted checkout.
  3. Stripe fires `checkout.session.completed` to our webhook; we read
     client_reference_id and flip the event to active.

If Stripe isn't configured (config.PAYMENTS_ENABLED is False) the app never
calls into here for real — events activate instantly in stub mode.
"""

from urllib.parse import quote

import config


# Add-ons bought after signup carry a prefix so the webhook knows what was paid.
INVITE_REF_PREFIX = "inv_"
PHOTOS_REF_PREFIX = "pho_"


def link_for(product: str) -> str:
    """The Payment Link for a product: photos, invite or bundle ("" if not set up)."""
    return {
        "photos": config.STRIPE_PAYMENT_LINK_URL,
        "invite": config.STRIPE_INVITE_LINK_URL,
        "bundle": config.STRIPE_BUNDLE_LINK_URL,
    }.get(product) or ""


def payment_url(event, product: str | None = None, ref_prefix: str = "") -> str:
    """Stripe's hosted Payment Link for this event, tagged with its id."""
    base = link_for(product or event.plan or "photos")
    sep = "&" if "?" in base else "?"
    url = f"{base}{sep}client_reference_id={ref_prefix}{event.id}"
    if event.contact_email:
        url += f"&prefilled_email={quote(event.contact_email)}"
    return url


def verify_webhook(payload: bytes, sig_header: str | None):
    """Verify the Stripe signature and return the parsed event, or raise."""
    import stripe  # imported lazily so the app runs without the lib in stub mode

    return stripe.Webhook.construct_event(
        payload, sig_header, config.STRIPE_WEBHOOK_SECRET
    )
