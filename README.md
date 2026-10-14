# Confetti — wedding photo-sharing SaaS

Multi-tenant photo/video sharing for weddings. A couple pays once, gets a
private gallery + a QR code; guests scan and upload straight to the couple's
storage — no app, no accounts.

## Architecture

| Layer | Choice |
|-------|--------|
| API | FastAPI (`main.py`) |
| Tenancy | one `events` row per couple (`db.py`, Postgres) |
| Storage | one Cloudflare R2 bucket, isolated per event under `events/<id>/` (`storage.py`) |
| Uploads | presigned PUT — browser → R2 directly, server never in the data path |
| Auth | per-event gallery password → signed session cookie (`security.py`) |
| Payments | Stripe Payment Link + webhook (`payments.py`); falls back to stub mode |

### Routes
- `/` landing · `/create` sign-up · `/create/success` post-payment
- `/e/<slug>` guest upload · `/e/<slug>/gallery` couple gallery · `/e/<slug>/qr.png`
- `POST /api/events`, `POST /api/stripe/webhook`, `POST /api/admin/cleanup`
- `/api/e/<slug>/...` — info, upload-url, login/logout/me, photos, download-zip, delete

Maps to the board: **WED-2** multi-user · **WED-4** dynamic names · **WED-5**
per-couple storage · **WED-6** auto-delete · **WED-7** pay-to-unlock.

## Local setup

1. `pip install -r requirements.txt`
2. `cp .env.example .env` and fill it in. **You must add a `DATABASE_URL`** —
   the app won't boot without Postgres now. A free [Neon](https://neon.tech)
   database works. For local http testing set `COOKIE_SECURE=false`.
3. Leave the two `STRIPE_*` vars blank to run in **stub mode** (galleries
   unlock instantly, no payment) so you can test the whole flow.
4. `uvicorn main:app --reload` → http://localhost:8000

## Going live with payments

1. In Stripe, create a **Payment Link** for your product (set the price there).
2. Add a webhook endpoint → `https://<your-domain>/api/stripe/webhook`,
   event `checkout.session.completed`. Copy its signing secret.
3. Set `STRIPE_PAYMENT_LINK_URL` and `STRIPE_WEBHOOK_SECRET` in `.env`.
   Now `/create` redirects to Stripe and the webhook activates the gallery.

## Auto-delete (WED-6)

`POST /api/admin/cleanup` deletes every event past `expires_at` (paid_at +
`EVENT_TTL_DAYS`, default 365) along with all its R2 objects. Protect it with
`ADMIN_TOKEN` and call it on a schedule (Render Cron / GitHub Action):

```bash
curl -X POST -H "X-Admin-Token: $ADMIN_TOKEN" https://<your-domain>/api/admin/cleanup
```

## Costs & pricing

See [PRICING.md](PRICING.md) — marginal cost is ~€2–4 per wedding (R2 has zero
egress fees), so a €15–19 one-time price undercuts the ~$49 market and still
profits.
