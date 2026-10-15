# Pricing model — "stupidly cheap" POC

Research + cost model backing WED-7. Goal: price low enough to win on price
while still turning a margin on every sale.

## What it actually costs to run one wedding (marginal)

Assume a gallery holds **5–15 GB** for its **1-year** life (a heavy wedding is
~800 photos + a handful of videos ≈ 6–10 GB).

| Cost | Rate | Per wedding / year |
|------|------|--------------------|
| R2 storage | $0.015 / GB-month | 5 GB → **$0.90**, 15 GB → **$2.70** |
| R2 writes (uploads/lists, Class A) | $4.50 / million | < **$0.01** |
| R2 reads (views/downloads, Class B) | $0.36 / million | < **$0.01** |
| **R2 egress** (incl. full-gallery zip) | **$0.00** | **$0.00** ← the whole game |
| Postgres row (Neon free tier) | free | **$0.00** |
| Stripe fee (on a €19 charge) | ~2.9% + €0.30 | ~**€0.85** |

➡ **Marginal cost ≈ €2–4 per wedding.** R2's zero egress is what makes it
cheap: guests stream previews and the couple downloads a multi-GB zip for free.

## Fixed overhead

| Piece | Free option | Paid (recommended for a live event) |
|-------|-------------|--------------------------------------|
| Hosting (Render web service) | $0 — but spins down after 15 min idle (~1 min cold start) | **$7/mo** Starter, always-on |
| Database (Neon Postgres) | $0 — 0.5 GB holds hundreds of thousands of event rows | — |

➡ **$0 to launch the POC.** Upgrade Render to $7/mo (~$84/yr) once real
couples depend on it being instant.

## The market

Competitors charge **$25–$200**, with the popular tier around **$49** one-time
(Guestpix $49, Dearest $50, Fotify $29.99–$49.99, PartyCam $25–$200).

## Recommendation

- **Launch price: €25, one-time, gallery live for 1 year.** Raised from the €15–19 first proposed; still about half the ~$49 market.
  Undercuts the $49 market leader by ~60% — the "stupidly cheap" hook — while
  still ~€15 gross margin per sale.
- **Break-even:** ~6 sales/year covers always-on hosting; everything after is profit.
- **Later tiers** (once validated): Basic €19 / Plus €39 (more storage, longer
  retention, custom colours & couple photo, remove "Confetti" branding).

> The charged amount is set on the **Stripe Payment Link** in the dashboard —
> keep it in sync with the price shown on the landing/create pages.

Sources: [Cloudflare R2 pricing](https://egresscost.com/cloudflare/) · [Render pricing](https://www.srvrlss.io/provider/render/) · [Neon free tier](https://agentdeals.dev/vendor/neon) · [Guestpix](https://guestpix.com/weddings-pricing/) · [wedding app comparison](https://partycam.app/blog/best-qr-code-wedding-photo-apps-2026)
