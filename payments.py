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


def payment_url(event) -> str:
    """Build the redirect URL to Stripe's hosted Payment Link for this event."""
    base = config.STRIPE_PAYMENT_LINK_URL or ""
    sep = "&" if "?" in base else "?"
    url = f"{base}{sep}client_reference_id={event.id}"
    if event.contact_email:
        url += f"&prefilled_email={quote(event.contact_email)}"
    return url


def verify_webhook(payload: bytes, sig_header: str | None):
    """Verify the Stripe signature and return the parsed event, or raise."""
    import stripe  # imported lazily so the app runs without the lib in stub mode

    return stripe.Webhook.construct_event(
        payload, sig_header, config.STRIPE_WEBHOOK_SECRET
    )
