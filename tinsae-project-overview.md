# Tinsae — Project Overview

*A context document for the whole project: what it is, why it exists, how it's built, and where it stands.*

> **Provenance note:** this is assembled from our planning conversations (July 12 → August 20), not from reading the repository. The product thinking and standing decisions are reliable. The "current state" section is the weakest part — we established on Aug 14 that the tickets had drifted from the actual code. Verify Section 9 against the repo before treating it as fact.

---

## 1. What Tinsae is

**Tinsae is a local product-discovery marketplace for the Ethiopian market.** Buyers search across many shops in one place and compare prices; shops get an online presence without building one.

It is **discovery-first, not transactional**. There is no cart, no checkout, no payments, no delivery in v1. A buyer finds a product, sees the price and which shop has it, and contacts that shop directly. The long-term path is to grow into a full marketplace, but every v1 decision assumes discovery only.

**One-line pitch:** *Find out who actually has it, and what they're actually charging — before you leave the house.*

---

## 2. The problem

In Addis (and Ethiopian cities generally):

- Buyers cannot compare prices across shops without physically visiting them or calling around.
- Most small shops have no online presence at all, or exist only as a Telegram channel with no search.
- There is no centralised place to discover what's available locally.

The result is wasted time for buyers and missed customers for sellers.

**The deeper problem — and the one that kills competitors:** listing sites go stale. A marketplace full of products that were sold three months ago is worse than no marketplace, because it burns buyer trust on the first visit. This is the failure mode Tinsae is specifically designed around.

---

## 3. The wedge: freshness

This is the core product idea and the thing that distinguishes Tinsae from a generic Jiji clone.

**Every product carries a `last_confirmed_at` timestamp. Two things read it: a stored `confirmation_status` column that the sweep maintains and that buyers see, and a finer tier derived at read time that is used only to rank results. Products past the clock are demoted, not hidden.**

The derived tier, from `catalog/freshness.py`. Ranking only — buyers are never shown these values:

| Tier | Window since last confirmation | Used for |
|---|---|---|
| `fresh` | ≤ 48 hours | Ranked first |
| `almost_expired` | ≤ 7 days | Ranked below fresh |
| `expired` | older, or never confirmed | Ranked last, still listed |

The stored column, `Product.confirmation_status`, on a 30-day clock maintained by `sweep_freshness`. This is the label buyers actually receive:

| Status | Meaning |
|---|---|
| `fresh` | Confirmed inside the window |
| `stale` | Past the window, due to be asked |
| `unconfirmed` | Asked, and the merchant never answered |

Shops keep products alive by confirming them — one tap in the dashboard, or one tap in Telegram. Confirming "yes, still available" and confirming "no, it sold" are *both* confirmations: availability and freshness are separate axes, and a shop telling you something sold is a shop engaging with reality. Both set `last_confirmed_at = now`.

**Why this matters commercially:** a buyer who visits and finds three phones that are all actually in stock at the price shown comes back. That's the entire retention thesis. Both sets of thresholds are product decisions, deliberately centralised as constants — the ranking windows in `catalog/freshness.py`, the confirmation clock as `PRODUCT_STALENESS_DAYS` in settings — so they can be tuned after talking to real shops.

---

## 4. Market and positioning

- **Geography:** Addis Ababa first, then nationwide.
- **Category:** phones + accessories only in v1. Two top-level categories, no nesting beyond one level. Deliberately narrow — deep in one vertical beats shallow across many.
- **Sellers:** small local shops, most of which already operate on Telegram.
- **Buyers:** anonymous. No buyer accounts in the MVP.
- **Telegram is a first-class channel, not a gimmick.** Ethiopian shops live on Telegram; a bot prompt is dramatically lower friction than asking a shopkeeper to log into a dashboard.

**Competitive landscape:** Amazon and eBay are irrelevant here. Jumia is the regional reference point. The opening is local focus, real price comparison, and onboarding simple enough for a shop with one employee — plus freshness, which none of them enforce.

---

## 5. Revenue model

Nothing is monetised in the MVP. The intended sequence:

1. **Featured / promoted listings** — shops pay for placement in search results.
2. **Advertising** — sponsored slots (primary long-term revenue).
3. **Seller subscriptions** — premium analytics and visibility tools.
4. **Transaction commission** — only once the platform actually handles transactions. Far future.

---

## 6. Standing design decisions

These are settled. Re-opening one should be a deliberate act, not a drift.

**Design A — a `Product` *is* one shop's priced offer.**
There is no separate `Listing` entity and no shared canonical catalog product. "Galaxy A15" sold by three shops is three `Product` rows. This was contested (Design B — a canonical Product with per-shop Listings — gives better cross-shop comparison), and **Design A won because it's what got built.** The cost is real and worth knowing: cross-shop comparison degrades toward string matching rather than grouping by a shared product ID. Migrating to B later is possible but not cheap.

**Freshness is derived, never stored.**
No `confirmation_status` column. A stored copy of a derivable value drifts, and then you have two sources of truth and no rule for which wins. The derivation rule lives in exactly one module (`catalog/freshness.py`) with the thresholds as constants.

**One write path for confirmation.**
`confirm_freshness()` and the availability transitions live in a single service in the domain layer, so the web dashboard and the Telegram bot cannot diverge. No view or bot handler mutates those fields directly.

**Web-first; Telegram is an additive surface.**
The bot is a second way to call a service that already exists. It is not on the MVP critical path. (Reality note: the bot shipped before the web dashboard, which is a deviation from this plan — see Section 9.)

**The architecture is boring on purpose.**
Modular monolith, Postgres, no microservices, no GraphQL, no Kafka, no Storybook, no monorepo tooling. Two principles held throughout: *boring means debuggable at 2 AM*, and *optimise for change, not for scale* — v1 will be wrong about many things and needs to be cheap to rewrite, not cheap to scale to a million users.

**Verification is manual.** A human flips `is_verified` on a shop. No automated KYC.

---

## 7. Tech stack

**Backend — `tinsae-backend`**
- Django 5 + Django REST Framework, Python 3.12
- PostgreSQL (local via Docker; Neon in production)
- JWT auth via `djangorestframework-simplejwt`
- `python-telegram-bot` for the bot (long-polling locally, webhook in production)
- `django-q2` for background jobs (Celery/Redis deferred to Go Live)
- Split settings under `config/settings/`, versioned URLs at `/api/v1/`
- Tooling: ruff, black, mypy + django-stubs, pytest, pre-commit

**Frontend — `tinsae-frontend`**
- Next.js 15 (App Router) + TypeScript
- Tailwind CSS + shadcn/ui
- Buyer site and shop dashboard are **route groups in one app**, not separate projects
- All API calls funnelled through `lib/api/`

**Infrastructure (planned, Go Live phase)**
- Railway (backend) + Vercel (frontend) + Neon (Postgres) + Cloudflare R2 (images) + Cloudflare CDN
- Upstash Redis, Sentry, UptimeRobot
- Budget target: roughly $30–80/month at MVP scale
- Hosting region: Europe (Frankfurt). Ethiopian data-localisation rules apply to financial records, which Tinsae doesn't handle.

**Process**
- Two repos, GitHub org `tinsae-et`, branch protection on `main` (PR + 1 approval)
- CI on both repos: lint, typecheck, migration check, tests. CD deferred.
- Work tracked in **Linear**, teams: Backend + Frontend

---

## 8. Architecture

```
                      Cloudflare CDN
                            |
        +-------------------+-------------------+
        |                   |                   |
   Buyer pages         Shop dashboard      Telegram bot
   (Next.js)           (Next.js)           (long-running process)
        |                   |                   |
        +-------------------+-------------------+
                            |
                   Django + DRF API
              (models, API, admin, bot handlers)
                            |
        +-------------------+-------------------+
        |                   |                   |
    Postgres             Redis            R2 (images)
                            |
                   Background worker
              (freshness sweep, reminders)
```

Three running processes total: API, worker, bot. One codebase.

**Backend apps**

| App | Owns |
|---|---|
| `core` | `BaseModel` (UUID pk, timestamps), pagination. Imports nothing. |
| `catalog` | `Category`, `Product`, product images, `freshness.py`, `services.py` |
| `shops` | `Shop` — the seller/tenant: verification, ownership, Telegram linking |
| `bot` | Telegram handlers, `run_bot` command, `ConfirmationRequest`, the sweep |
| `inquiries` | buyer contact-click tracking |
| `jobs` | scheduled tasks |

The rule that keeps this clean: **domain logic lives in `catalog`/`shops`; anything Telegram-shaped — prompt text, message sending — stays in `bot`.** The bot is a delivery mechanism, not a place where business rules live.

**Frontend structure**

```
app/
  (buyer)/     -> search/home, product detail, shop page   [public]
  dashboard/   -> my products, add/edit, confirm, profile  [login required]
components/    -> by feature: product/, shop/, search/, shared/, ui/
lib/api/       -> single API client
```

---

## 9. Where the project stands

**⚠️ This section is the least reliable part of this document.** It reflects what we discussed, not what's in the repo. Confirm before relying on it.

**Believed complete**
- TIN-1 → TIN-10, the bootstrap epic: both repos, Docker, Postgres, health endpoint, CI green on both sides, local end-to-end chain working.
- `core`, `catalog` (Category + Product, seed command, admin, read-only API), `shops`.
- Custom `User` with `telegram_chat_id`, JWT token + refresh endpoints, `owner` FK on Shop.
- The Telegram bot, including `apply_confirmation`, `sweep_freshness`, and `ConfirmationRequest`.
- Domain confirmation write path (`catalog/services.py:apply_confirmation`), shared across Web and Telegram.
- Web merchant transitions (`confirm`, `mark_sold`, `pause`, `resume`, `confirm_all`).
- Cross-shop isolation hardening and comprehensive adversarial audit test suite.
- Concurrency-safe slug retry engine (`core/slugs.py`) and full 341-test suite.

**Known deviation:** the bot shipped ahead of the web dashboard, reversing the planned web-first order. Worth confirming the web dashboard is still a committed deliverable rather than something that quietly fell off.

**Known open work**
- The web dashboard: shell, product CRUD, image upload, the confirm button.
- Background worker scheduling (`sweep_freshness`) and production Redis cache/R2 storage integration.

**Planned backlog shape (TIN-1 → TIN-40, four epics of ten)**

1. **TIN-1 → 10 · Bootstrap** — repos, Docker, Postgres, CI
2. **TIN-11 → 20 · Data model & discovery** — Shop, Category, Product, search/filter, images
3. **TIN-21 → 30 · Freshness system** — bot, confirmation flow, staleness sweep, freshness-aware ranking
4. **TIN-31 → 40 · Auth & shop self-serve** — dashboard, product CRUD, web confirmation, isolation hardening, onboarding

After 40: **Go Live** (deploy, webhooks, Celery/Redis, domain, security hardening), then growth.

---

## 10. Open decisions

- **Does a shop need "I'm closed this week"?** If yes, it's a Shop-level concept, not a field on Product.
- **The back-in-stock re-prompt path** exists in the bot but may be unreachable in production. Either ticket it as a real feature or delete it.
- **Availability representation** — `is_available` boolean vs `status` enum (`active`/`paused`/`sold`). Decide and document; the dashboard depends on it.
- **When do phone filters stop being hardcoded?** `Product.specs` is a JSONField from day one, but there's no attribute registry or dynamic filter UI. That's v2, when a second category arrives.

---

## 11. Working conventions

- **Estimates:** 1 ≈ half a day · 2 ≈ a day · 3 ≈ ~two days.
- **Tickets are written for juniors** — acceptance criteria should be checkable, not aspirational. "No view mutates this field directly" is a review convention, not a testable deliverable; don't dress conventions up as acceptance criteria.
- **Real dependencies, not invented ones.** The data model is the most upstream thing in the system; nothing frontend blocks it.
- **Pure functions in their own modules** (`freshness.py`, `search.py`) so they're testable without the ORM.
- **Never float for money.** `DecimalField`, currency defaults to ETB.
- **Boundary tests on freshness** — exactly 48h, exactly 7d, and null.

---

## 12. Glossary

| Term | Meaning |
|---|---|
| **Product** | One shop's priced offer of one item. The core entity (Design A). |
| **Shop** | The seller/tenant. Owns products, can be verified, links to a Telegram account. |
| **Freshness** | Derived tier (`fresh` / `almost_expired` / `expired`) computed from `last_confirmed_at`. |
| **Confirmation** | A shop answering "is this still available?" — sets `last_confirmed_at = now` regardless of the answer. |
| **Sweep** | The scheduled job that finds stale products, prompts their shops, and expires unanswered prompts. |
| **Design A / Design B** | A: Product *is* the offer (chosen). B: canonical Product + per-shop Listing (rejected). |
| **TIN-n** | Linear issue. Numbers in planning docs are names, not addresses — Linear assigns the real ones. |

---

*Founder: Solomon Adola. Living document — update as the codebase moves.*
