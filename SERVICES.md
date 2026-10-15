# Services

Every outside service Confetti depends on, what it's for, and where it's
configured. No secrets live here; they are in each service's dashboard and in
the Render environment variables.

## At a glance

| Service | Use case | Plan / cost |
|---------|----------|-------------|
| [GitHub](#github) | Source code; every push deploys | Free |
| [Render](#render) | Runs the app (production + staging) | Starter $7/mo (production), Free (staging) |
| [Neon](#neon) | Postgres database (galleries, reset links) | Free |
| [Cloudflare R2](#cloudflare-r2) | Stores every photo and video | ~$0.015/GB-month, no download fees |
| [Stripe](#stripe) | Takes the €25 payment | ~1.5–3% + €0.25 per sale |
| [Resend](#resend) | Sends welcome and password-reset emails | Free (3,000 emails/mo) |
| [Namecheap](#namecheap) | Owns `confettiwedding.net` and its DNS | ~€13/yr |
| [ImprovMX](#improvmx) | Forwards `support@` to Gmail | Free |
| [Jira](#jira) | Task board (project `WED`) | Free |
| Google Fonts | Cormorant Garamond + Inter on every page | Free |

## How a wedding flows through them

1. A couple opens **confettiwedding.net** (Namecheap DNS → Render).
2. They sign up; the gallery is saved in **Neon**.
3. They pay on **Stripe**; Stripe calls our webhook and the gallery unlocks.
4. **Resend** emails them a welcome with the gallery link and QR code.
5. Guests scan the QR and upload straight to **Cloudflare R2** (the files never pass through Render).
6. The couple views and downloads everything from R2.
7. Replies to our emails go to `support@`, which **ImprovMX** forwards to Gmail.

---

## GitHub
- **Repo:** `panayiotissss/wedding`
- **Branches:** `main` = production; the working branch deploys to staging.
- Render watches both and redeploys on every push.

## Render
Runs the FastAPI app. Workspace **HawkX**, project **WedAPP**, region Frankfurt.

| Service | Branch | URL | Plan |
|---------|--------|-----|------|
| `wedding-photos` (Production) | `main` | https://confettiwedding.net (also wedding-photos-xpgh.onrender.com) | Starter, always on |
| `wedding-photos-staging` (Staging) | working branch | https://wedding-photos-staging.onrender.com | Free, sleeps when idle |

Environment variables (values only in Render):

| Variable | What it is | Production | Staging |
|----------|-----------|------------|---------|
| `DATABASE_URL` | Neon connection | `production` branch | `staging` branch |
| `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_BUCKET`, `R2_ENDPOINT` | Cloudflare R2 | same bucket | same bucket |
| `R2_KEY_ROOT` | Top folder in the bucket | `events` (default) | `staging/events` |
| `SESSION_SECRET` | Signs log-in cookies | own value | own value |
| `PUBLIC_BASE_URL` | Used in QR codes and email links | `https://confettiwedding.net` | staging URL |
| `STRIPE_PAYMENT_LINK_URL`, `STRIPE_WEBHOOK_SECRET` | Stripe | live mode | test mode |
| `FREE_ACCESS_CODE` | Code that skips payment | set in Render | `STAGING-FREE` |
| `RESEND_API_KEY` | Sends email | send-only key | same key |
| `ADMIN_TOKEN` | Protects `/api/admin/cleanup` | set in Render | set in Render |
| `EMAIL_FROM`, `EMAIL_REPLY_TO`, `EMAIL_DNS_CHECK`, `EVENT_TTL_DAYS` | Optional, have defaults | defaults | defaults |

## Neon
Postgres. Project **Wedding** (`plain-scene-07702196`), region eu-central-1.

| Branch | Used by |
|--------|---------|
| `production` | Production app |
| `staging` | Staging app (copy of production taken 15 Oct 2026) |

Tables: `events` (one row per gallery) and `password_resets` (one-time links,
only hashes stored). Both are created automatically on startup.

## Cloudflare R2
Photo and video storage. One bucket; every gallery has its own folder:
`events/<couple-name>-<id>/photos/<guest>/...` in production and
`staging/events/...` on staging. The zip for "Download all" is cached under
`.../exports/`. R2 charges nothing for downloads, which is what keeps the
price at €25.

## Stripe
Account **Confetti**.
- **Live mode** (production): €25 Payment Link that redirects to
  `/create/success`; webhook → `https://<production>/api/stripe/webhook`
  for `checkout.session.completed`.
- **Test mode / sandbox** (staging): same setup pointing at the staging URL.
  Test card `4242 4242 4242 4242`.
- Each checkout carries the gallery id (`client_reference_id`), so the webhook
  knows which gallery to unlock.

## Resend
Transactional email from `Confetti <hello@confettiwedding.net>`, replies to
`support@confettiwedding.net`. Domain verified in region eu-west-1. Emails:
welcome (after payment or free code) and password reset. Without
`RESEND_API_KEY` the app only logs emails (local dev, tests).

## Namecheap
Registrar for **confettiwedding.net** (privacy on, auto-renew). Mail Settings
must stay on **Custom MX**.

| Type | Host | Value | For |
|------|------|-------|-----|
| A | `@` | `216.24.57.1` | Render (website) |
| CNAME | `www` | `wedding-photos-xpgh.onrender.com` | Render (redirects to root) |
| TXT | `resend._domainkey` | DKIM key | Resend |
| MX | `send` | `feedback-smtp.eu-west-1.amazonses.com` (10) | Resend |
| TXT | `send` | `v=spf1 include:amazonses.com ~all` | Resend |
| CNAME | `rsend` | `send.forge.rmta.net` | Resend |
| TXT | `_dmarc` | `v=DMARC1; p=none;` | Email trust |
| MX | `@` | `mx1.improvmx.com` (10), `mx2.improvmx.com` (20) | ImprovMX |
| TXT | `@` | `v=spf1 include:spf.improvmx.com ~all` | ImprovMX |

## ImprovMX
Free email forwarding: `support@confettiwedding.net` (and a catch-all `*@`)
→ the owner's Gmail. There is no mailbox; it only forwards.

## Jira
Board `WED` on panayiotis.atlassian.net. Ticket numbers appear in commit
messages and code comments (e.g. WED-6 auto-delete, WED-21 emails).

---

## When something changes
- **New domain:** update Namecheap DNS, Render custom domains,
  `PUBLIC_BASE_URL`, the Stripe Payment Link redirect and webhook URL, and the
  Resend domain.
- **Rotating a secret:** change it in the service, then in Render's environment
  variables (both services if shared).
- **Adding a service:** add a row here.
