"""
Transactional email via Resend (https://resend.com), sent from the Confetti
domain. Uses the standard library so there's no extra dependency.

Without RESEND_API_KEY (local dev, tests) emails are logged, not sent.
Sending never raises: a failed email must not break signup or payment.
"""

import json
import logging
import urllib.request
from html import escape

import config

log = logging.getLogger("confetti.mailer")


def send(to: str, subject: str, html: str, text: str) -> bool:
    if not config.RESEND_API_KEY:
        log.info("Email not sent (no RESEND_API_KEY): to=%s subject=%s", to, subject)
        return False
    body = json.dumps({
        "from": config.EMAIL_FROM,
        "to": [to],
        "subject": subject,
        "html": html,
        "text": text,
        "reply_to": config.EMAIL_REPLY_TO,
    }).encode()
    req = urllib.request.Request(
        "https://api.resend.com/emails",
        data=body,
        headers={
            "Authorization": f"Bearer {config.RESEND_API_KEY}",
            "Content-Type": "application/json",
            "User-Agent": "confetti-app",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return 200 <= resp.status < 300
    except Exception:
        log.exception("Email send failed: to=%s subject=%s", to, subject)
        return False


# ── Templates ──────────────────────────────────────────────────────────────

def _layout(inner: str) -> str:
    return f"""<!doctype html><html><body style="margin:0;background:#FBF7F2;font-family:-apple-system,Segoe UI,Helvetica,Arial,sans-serif;color:#2B2420">
<table width="100%" cellpadding="0" cellspacing="0"><tr><td align="center" style="padding:32px 16px">
<table width="100%" cellpadding="0" cellspacing="0" style="max-width:520px;background:#fff;border:1px solid #EADFD5;border-radius:18px">
<tr><td style="padding:32px 28px">
<table cellpadding="0" cellspacing="0" style="margin-bottom:22px"><tr>
<td><img src="{config.PUBLIC_BASE_URL}/static/logo.png" width="36" height="36" alt="" style="display:block;border-radius:9px"></td>
<td style="padding-left:10px;font-family:Georgia,serif;font-size:22px;font-weight:600">Confetti</td>
</tr></table>
{inner}
</td></tr></table>
<p style="color:#8C8077;font-size:12px;margin-top:16px">Confetti · Made with ❤ for the people getting married</p>
</td></tr></table></body></html>"""


def _button(url: str, label: str) -> str:
    return (f'<a href="{escape(url)}" style="display:inline-block;background:#9E4F45;color:#fff;'
            f'text-decoration:none;font-weight:600;padding:13px 24px;border-radius:999px">{escape(label)}</a>')


def welcome(to: str, names: str, gallery_url: str, qr_url: str) -> bool:
    n = escape(names)
    html = _layout(f"""
<h1 style="font-family:Georgia,serif;font-size:26px;margin:0 0 8px">Your gallery is ready 🎉</h1>
<p style="color:#5C514A;line-height:1.55">{n}, congratulations! Here's everything you need.</p>
<p style="margin:24px 0">{_button(gallery_url, "Open my gallery")}</p>
<p style="color:#5C514A;line-height:1.55"><b>Your QR code:</b> print it on table cards, menus or a sign.
Guests scan it and their photos land in your gallery.<br>
<a href="{escape(qr_url)}" style="color:#9E4F45">Download your QR code</a></p>
<p style="color:#5C514A;line-height:1.55"><b>Logging in:</b> use this email ({escape(to)}) and the password you chose.</p>""")
    text = (f"Your gallery is ready, {names}!\n\nOpen your gallery: {gallery_url}\n"
            f"Download your QR code: {qr_url}\n\nLog in with {to} and the password you chose.")
    return send(to, "Your Confetti gallery is ready", html, text)


def password_reset(to: str, reset_url: str) -> bool:
    html = _layout(f"""
<h1 style="font-family:Georgia,serif;font-size:26px;margin:0 0 8px">Reset your password</h1>
<p style="color:#5C514A;line-height:1.55">Someone asked to reset the password for your Confetti gallery.
If that was you, choose a new one below. The link works for 1 hour.</p>
<p style="margin:24px 0">{_button(reset_url, "Choose a new password")}</p>
<p style="color:#8C8077;font-size:13px;line-height:1.5">If you didn't ask for this, ignore this email. Your password stays the same.</p>""")
    text = (f"Reset your Confetti password: {reset_url}\n\nThe link works for 1 hour. "
            "If you didn't ask for this, ignore this email.")
    return send(to, "Reset your Confetti password", html, text)
