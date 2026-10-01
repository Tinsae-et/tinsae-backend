# Tinsae Backend

Backend API for **Tinsae** — a local product-discovery marketplace.
Django 5.1 + Django REST Framework + PostgreSQL, fully containerized.

## Architecture overview

The project follows Django's **app-per-domain** pattern. Each app owns one
domain concept and has a clear dependency direction:

```
users ──> shops ──> catalog ──> bot
  │
  └── provides authentication + ownership
```

### Apps and their responsibilities

| App | Role | Depends on |
|---|---|---|
| `users` | Custom User model, auth, merchant profiles | nothing |
| `shops` | Shop CRUD, merchant storefronts | `users` (owner FK) |
| `catalog` | Categories, products (read-only catalog) | `core` (BaseModel) |
| `bot` | Telegram confirmation prompts, the freshness sweep | `catalog`, `shops` |
| `ops` | Internal loop-health metrics (staff-only) | `bot`, `catalog`, `shops` |
| `core` | Shared utilities (BaseModel, pagination) | nothing |
| `health` | Liveness / readiness probe | nothing |

### App dependency rules

- An app may **import models** from apps listed above it (downward).
- An app must **never import** from apps below it (upward).
- `core` and `health` are leaf utilities — no app depends on them for
  business logic, but any app can optionally inherit from `core.BaseModel`.
- This prevents circular imports and keeps the dependency graph a DAG.

## Key design decisions

### Custom User model from day one

`users.User` extends `AbstractUser` with `phone` and `telegram_chat_id`.
This is set as `AUTH_USER_MODEL` in settings — it's a **one-way door** that
must be done before the first migration. Benefits:

- No extra JOIN to a profile table.
- Clean FK relationships: `shop.owner` points directly to the user.
- Telegram integration fields are baked in from the start.

### Ownership and permissions

Shops are owned by users (non-nullable `owner` FK). The permission model
is simple and consistent:

```
Anonymous         GET only (public catalog, shop listings)
Authenticated     GET + manage own resources
Owner             PATCH / DELETE their own shop
Staff             bypasses all object permissions
```

Custom permissions (`IsOwnerOrReadOnly`, etc.) are small, composable classes
in each app's `permissions.py`.

### API versioning and URL layout

```
/api/health/                    ← liveness (no version, never changes)
/api/v1/auth/token/             ← JWT obtain
/api/v1/auth/token/refresh/     ← JWT refresh
/api/v1/catalog/categories/     ← read-only
/api/v1/catalog/products/       ← read-only (filterable by ?category=)
/api/v1/shops/                  ← CRUD, owner-gated
/api/v1/ops/health/             ← internal loop metrics (staff only)
/ops/health/                    ← the same metrics as an admin page (staff only)
/admin/                         ← Django admin
```

Versioning is path-based (`/api/v1/`) — simple, visible, cache-friendly.
New versions get `/api/v2/` without breaking existing clients.

### Settings strategy

`config/settings/` uses a 3-tier split:

```
base.py   ← shared by all environments (DB, auth, installed apps, DRF config)
dev.py    ← DEBUG on, relaxed hosts, local overrides
prod.py   ← DEBUG off, SSL, HSTS, hardened security
```

`base.py` reads `.env` via `django-environ`. Every environment uses the
same `DATABASE_URL` format — no SQLite fallback for dev/prod parity.

### BaseModel

Every domain model gets `created_at` + `updated_at` timestamps for free by
inheriting `core.models.BaseModel`:

```python
class BaseModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True
        ordering = ["-created_at"]
```

This is an **abstract** model — it creates no table of its own, just adds
columns to child tables.

### Authentication flow

JWT (via `rest_framework_simplejwt`) is the sole authentication scheme:

```
1. POST /api/v1/auth/token/        ← credentials → access + refresh tokens
2. POST /api/v1/auth/token/refresh/ ← refresh token → new access token
3. All API requests carry:  Authorization: Bearer <access_token>
```

The `JWTAuthentication` class is set as the DRF default, so protecting an
endpoint is as simple as adding a permission class — no per-view wiring.

## Recommended workflow: adding a new feature

1. **Create the app** — `python manage.py startapp <domain>`
2. **Define models** — inherit `BaseModel` from `core` if you need timestamps.
3. **Register in admin** — with sensible `list_display`, `list_filter`,
   `search_fields`.
4. **Make + apply migrations** — `makemigrations <app> && migrate`.
5. **Create serializers** — one per response shape (detail vs. list).
6. **Create ViewSet** — use `ModelViewSet` or `ReadOnlyModelViewSet`.
7. **Add permissions** — at minimum `IsAuthenticatedOrReadOnly` for write
   endpoints.
8. **Wire URLs** — `DefaultRouter` in `urls.py`, include from `config/urls.py`.
9. **Test** — at least one test per model and per API endpoint.
10. **Format + lint** — `ruff check . --fix && black .` before committing.

## Data model

```
┌──────────┐     ┌──────────────────┐     ┌─────────────────┐
│   User   │     │      Shop        │     │    Category     │
├──────────┤     ├──────────────────┤     ├─────────────────┤
│ username │──┐  │ name             │     │ name            │
│ email    │  │  │ slug (unique)    │     │ slug (unique)   │
│ password │  └─>│ owner ───────────│──   │ parent (self FK)│
│ phone    │     │ city             │     │ display_order   │
│ tg_chat  │     │ is_verified      │     └────────┬────────┘
└──────────┘     │ created_at       │              │
                  │ updated_at       │              │
                  └──────────────────┘     ┌────────┴────────┐
                                           │    Product      │
                                           ├─────────────────┤
                                           │ brand           │
                                           │ name            │
                                           │ slug (unique)   │
                                           │ specs (JSON)    │
                                           │ category ───────┘
                                           └─────────────────┘
```

## Code style

- **Formatter:** Black (line length 88, target py312).
- **Linter:** Ruff (selects `E, F, I, B, C4, UP`; ignores `E501`).
- **File naming:** Django/DRF conventions — `urls.py`, `serializers.py`,
  `views.py`, `tests/` (package), `admin.py`.

```bash
ruff check . --fix && black .    # run before every push
```

## Getting started

### Prerequisites

- **Docker** (with Compose v2 — `docker compose version` should work)
- **Git**

### Setup

```bash
# 1. Clone and enter the repo
git clone <repo-url> tinsae-backend
cd tinsae-backend

# 2. Create your local environment file
cp .env.example .env

# 3. Generate a secret key and paste it into .env as DJANGO_SECRET_KEY
docker run --rm python:3.12-slim python -c "import secrets; print(secrets.token_urlsafe(50))"

# 4. Start the stack (Postgres + backend)
docker compose up --build
```

The API is then live at **http://localhost:8000**.

Verify it's healthy:

```bash
curl http://localhost:8000/api/health/
# -> {"status": "ok", "database": "ok"}
```

### How it runs

`docker compose up` starts two containers:

- **`db`** — PostgreSQL 16.
- **`backend`** — the Django app (Python 3.12). On start it runs migrations,
  then the dev server.

Your code is mounted into the `backend` container, so **editing a `.py` file
auto-reloads** the server — no rebuild needed. Rebuild only when dependencies
change (`docker compose up --build`).

## Common commands

```bash
docker compose up                          # start the stack
docker compose up --build                  # rebuild (after dependency changes)
docker compose down                        # stop and remove containers
docker compose logs -f backend             # tail the app logs
docker compose exec backend python manage.py migrate         # run migrations
docker compose exec backend python manage.py makemigrations  # create migrations
docker compose exec backend python manage.py createsuperuser # create admin user
docker compose exec backend python manage.py test -v 2       # run tests
```

## The confirmation loop

Tinsae keeps listings honest with a weekly check-in: a buyer should only see a
product whose shop has recently said it is still real. Shop owners live in
Telegram, so the bot takes the question to them.

- **One clock: 7 days due, 14 days hidden.** A listing unprompted for
  `CONFIRMATION_INTERVAL_DAYS` (7) is *due*; after `CONFIRMATION_HIDDEN_AFTER_DAYS`
  (14) without a confirmation it leaves buyer-visible search. Both answers —
  "still available" and "sold out" — restart the clock: freshness measures
  engagement, not stock.
- **One digest per shop, at 08:00 EAT**, listing only the products that are due
  (up to 15, then "…and N more"). Never one message per product.
- **Proactive messages go through an outbox** drained by a rate-limited sender, so
  a restart never loses a digest and Telegram's limits are respected.
- **Silence costs visibility, gently.** One reminder at 13:00; no answer by the
  14-day mark hides the listing. Nothing else changes.

### Commands & menu

The bot registers its commands with Telegram, so the client's ☰ / “/” menu lists
them. `/menu` opens an inline hub that reuses the same handlers.

| Command | Does |
| --- | --- |
| `/start` | Welcome, or link via `/start <code>` |
| `/menu` | Navigation hub (My products · Status · Help · Unlink) |
| `/myproducts` | The shop's full catalogue, paged |
| `/status` | Fresh / due / hidden counts + last check-in |
| `/unlink` | Disconnect this chat from the shop |
| `/link <code>` | Connect using a one-time code |
| `/help` | Usage guide |

Free-text replies: `all`, `sold 2 5`, or bare numbers (`1 3` prompts a clarifying
question rather than guessing).

### The freshness sweep

`sweep_freshness` expires unanswered requests and reconciles
`Product.confirmation_status` against the clock. It no longer sends anything — the
digest is the only thing that asks.

```bash
# Run manually:
docker compose exec backend python manage.py sweep_freshness

# Simulate — writes nothing, sends nothing:
docker compose exec backend python manage.py sweep_freshness --dry-run
```

The command is idempotent: re-running changes nothing until the clock moves. Run it
on the Celery Beat schedule (below), not by hand, in production.

### Preview a digest

See exactly what a shop would receive before anything sends:

```bash
docker compose exec backend python manage.py preview_digest <shop-slug>
docker compose exec backend python manage.py preview_digest --all
```

Read-only: it prints the exact message and a `WOULD SEND` / `WOULD SKIP (<reason>)`
verdict, writing nothing.


### Configuration

Thresholds are settings, not flags: `confirmation_status` is a stored column, and
two runs at different thresholds would leave it disagreeing with the buyer-facing
status.

| Env var | Default | Meaning |
| --- | --- | --- |
| `CONFIRMATION_INTERVAL_DAYS` | `7` | Days until a listing is due (the "fresh" window) |
| `CONFIRMATION_HIDDEN_AFTER_DAYS` | `14` | Days until an unconfirmed listing is hidden |
| `TELEGRAM_BOT_TOKEN` | — | Required to reach Telegram at all |
| `TELEGRAM_BOT_USERNAME` | — | Bot handle (no `@`) for the deep link |
| `ENABLE_TELEGRAM_BOT_PROMPTS` | `False` | Master switch for **outbound** (digest + reminders) |
| `TELEGRAM_MOCK_SEND` | `False` | Log outbound instead of sending (plumbing tests) |
| `MYPRODUCTS_PAGE_SIZE` | `8` | Products per page in `/myproducts` |
| `QUIET_HOURS_START` / `QUIET_HOURS_END` | `21` / `7` | EAT hours when proactive sends pause |
| `PROCESSED_UPDATE_RETENTION_DAYS` | `7` | Update-dedupe rows kept |
| `OUTBOX_RETENTION_DAYS` | `30` | Terminal outbox rows kept |
| `TELEGRAM_LINK_CODE_TTL_HOURS` | `24` | How long a link code stays valid |
| `CELERY_BROKER_URL` | `redis://redis:6379/0` | Celery broker + result backend |
| `REDIS_URL` | — | Shared cache for throttle counters (Redis DB `/1`). Dev falls back to LocMemCache, which is per-process — set it whenever running more than one worker |

**Outbound needs both `ENABLE_TELEGRAM_BOT_PROMPTS=True` and a valid
`TELEGRAM_BOT_TOKEN`.** With the flag off — the default — `send_due_digests` and
`send_reminders` do nothing; the sweep still reconciles statuses and expires
requests. If no digest goes out and you expected one, check the flag first.

### What counts as stale

A listing is **due** when it is available **and** either `last_confirmed_at` is
older than `CONFIRMATION_INTERVAL_DAYS` or it has never been confirmed. A
never-confirmed listing is due from creation — the age of the listing is not
evidence its stock claim still holds.

Answered, paused and sold listings are outside confirmation: paused and sold are
`is_available = False`, so the digest never asks about them. "Sold out" is an
answer and restarts the clock; a paused listing makes no stock claim, so its clock
is left alone. A sold listing comes back with **🔄 Back in stock** (which restarts
the clock) and a paused one with **▶️ Resume** (which does not).

### Two switches, and what each one turns off

The bot has an outbound half and an inbound half, controlled separately.

| | Controls | Turned off by |
| --- | --- | --- |
| **Outbound** | `send_due_digests` and `send_reminders` (the weekly digest + one reminder) | `ENABLE_TELEGRAM_BOT_PROMPTS=False` (the default) |
| **Inbound** | `/start`, `/menu`, `/myproducts`, `/status`, button callbacks, text replies | not running `run_bot` (it refuses to start without a token) |

`/myproducts`, `/status`, `/menu` and the button callbacks are **not** gated on the
flag. It is outbound policy — "should we ping merchants?" — and a merchant who
opened the listing themselves is already talking to us.

Three sensible configurations:

| Bot process | Prompts flag | What you get |
| --- | --- | --- |
| not running | off | **Default.** Nothing sends, nothing listens. API, sweep and admin are unaffected. |
| running | off | No unsolicited digests, but merchants self-serve via `/myproducts`. Freshness data stays real. |
| running | on | The full loop: the digest prompts, merchants answer by button or text. |

### Running with the bot fully off

The default state, and a safe one — but keep the bot schedules unapplied while it
lasts.

Nothing runs on its own until you apply the schedules. The `bot` app just holds an
empty `ConfirmationRequest` table, and the dependency arrow points `bot → catalog`,
never back, so the public API never reaches this code.

**Do not apply the schedules while the bot is fully off.** With nothing inbound,
nothing writes `last_confirmed_at` apart from `seed_shop_data` and manual admin
edits. Because a NULL `last_confirmed_at` counts as due, a scheduled sweep would
walk the whole catalogue to `stale` within `CONFIRMATION_INTERVAL_DAYS` of the last
seed, and anything added through the admin would be stale on creation.

To make freshness mean something without outbound prompts, run the bot (it starts
as its own compose service) with the flag off and point merchants at `/myproducts`
— the middle row above.

- The frontend should not ship a default-on "fresh only" filter while
  `confirmation_status` is nobody's to maintain.
- Keep `python-telegram-bot` installed even if the bot is idle — `bot/tests.py`
  imports `run_bot`, so dropping it breaks the test suite.

### Bot schedules (Celery Beat)

`run_bot` registers the commands; proactive work and schedules live on Celery. Once
the bot is live, apply the periodic tasks (idempotent):

```bash
docker compose exec backend python manage.py setup_bot_schedules
```

That registers, all in `Africa/Addis_Ababa`:

| Task | When |
| --- | --- |
| `bot.tasks.drain_outbox` | every 15s |
| `bot.tasks.send_due_digests` | 08:00–11:00, hourly |
| `bot.tasks.send_reminders` | 13:00–16:00, hourly |
| `bot.tasks.sweep` | hourly |
| `bot.tasks.prune` | 03:00 |

The digest and reminder fire across a short window rather than a single minute,
so a beat restart or DNS blip at 08:00 catches up at 09:00 instead of losing the
day. Both are idempotent per shop per day (unique `(shop, for_date)`,
`reminded_at`), so the extra ticks send nothing.

To re-register the client command menu (the ☰ button):

```bash
docker compose exec backend python manage.py sync_bot_commands
```

### Telegram bot processes

Three long-running services, all started by `docker compose up`:

- **`bot`** (`manage.py run_bot`) — inbound: long-polling, commands, buttons.
  **Exactly one instance per token**; a second gets `409 Conflict`.
- **`celery-worker`** — drains the outbox and runs the scheduled tasks.
- **`celery-beat`** — the schedule.

`run_bot` exits with a `CommandError` if `TELEGRAM_BOT_TOKEN` is unset.

### `/myproducts` — the catalogue

A merchant sends `/myproducts` and gets their whole catalogue (drafts excluded),
paged `MYPRODUCTS_PAGE_SIZE` at a time, ordered **due → fresh → paused → sold**.
Buttons follow each product's state:

| State | Buttons |
| --- | --- |
| live and available (due or fresh) | ✅ Confirm · ❌ Sold · ⏸ Pause |
| paused (`LIVE`, unavailable) | ▶️ Resume · ❌ Sold |
| sold (`SOLD`) | 🔄 Back in stock |

- **Paused ≠ sold.** Pausing hides a listing without a stock claim, so the clock is
  left untouched — and resuming leaves it untouched too, so a pause/resume round
  trip cannot launder a stale listing fresh. "Sold out" is an answer and restarts
  the clock; "Back in stock" republishes a sold item and restarts it.
- **Ordering** is due first (never-confirmed leading), then fresh, paused, sold,
  with `pk` breaking ties so paging cannot drop or repeat a row.
- **Paging** edits the existing message rather than sending new ones.
- **Scoping** is derived from the inbound chat id only, never from button data —
  otherwise paging would become a way to read another merchant's catalogue. One
  chat may own several shops; all of them are listed, prefixed by shop name.
- **Stale buttons are no-ops.** Each action only applies from the state its button
  is offered in; a ✅ tapped on an out-of-stock product does nothing rather than
  silently republishing it.

Callback payloads are namespaced: `pconfirm` / `pdeny` / `pback` / `ppause` /
`presume`, each `<action>:<product_pk>:<page>`, plus `page:<n>` for navigation,
`menu:*` for the hub, and the digest/review families. Telegram caps
`callback_data` at 64 bytes, which is why the target is a single id rather than a
list.

Every callback is re-checked against the `telegram_chat_id` of the shop that owns
the product, so one merchant cannot act on another's. The listener keeps its
update offset in memory only; after a restart Telegram may re-deliver up to 24
hours of queued updates, and `ProcessedUpdate` drops duplicates before any handler
runs.

### Celery & Redis Background Processing

Celery handles background processing and periodic tasks with Redis as the broker and result backend. Periodic task schedules are stored in the database via `django-celery-beat`.

In Docker Compose, `redis`, `celery-worker`, `celery-beat`, and `bot` run as services alongside `backend` and `db`:

```bash
docker compose up -d redis celery-worker celery-beat
```

To run worker or beat manually inside the container:

```bash
# Run Celery worker
docker compose exec backend celery -A config worker -l INFO

# Run Celery beat scheduler
docker compose exec backend celery -A config beat -l INFO --scheduler django_celery_beat.schedulers:DatabaseScheduler
```

Or on the host (with local virtualenv active and local Redis container running):

> **Note**: Inside Docker containers, Redis is addressed via the internal service name `redis://redis:6379/0`. On the host machine, the dev port is exposed at `6380` (`redis://localhost:6380/0`), so host commands should point `CELERY_BROKER_URL` and `CELERY_RESULT_BACKEND` to port `6380`.

```bash
CELERY_BROKER_URL=redis://localhost:6380/0 CELERY_RESULT_BACKEND=redis://localhost:6380/0 celery -A config worker -l INFO
CELERY_BROKER_URL=redis://localhost:6380/0 CELERY_RESULT_BACKEND=redis://localhost:6380/0 celery -A config beat -l INFO --scheduler django_celery_beat.schedulers:DatabaseScheduler
```

#### Bot periodic schedules

The bot's schedules (digest, reminder, sweep, prune, outbox drain) are registered
by:

```bash
docker compose exec backend python manage.py setup_bot_schedules
```

See *Bot schedules (Celery Beat)* above for the full table.

#### Verifying Celery locally

```bash
docker compose exec backend python manage.py shell -c \
"from bot.tasks import smoke_heartbeat; print(smoke_heartbeat.delay().get(timeout=5))"
docker compose logs -f celery-worker
```


## Loop health check (TIN-32)

Visibility into whether the confirmation loop is actually working. Two surfaces,
one computation (`ops/metrics.py::collect_health`), so they cannot disagree:

| Surface | URL | Auth |
| --- | --- | --- |
| Admin page | `/ops/health/` | Django session, staff only |
| JSON endpoint | `/api/v1/ops/health/` | staff only; session **or** JWT |

Both are staff-gated and neither is part of the public `/api/v1` contract. The
`ops` app has **no models**, so it needs no migration.

### The daily check (30 seconds)

Open `/ops/health/` and read three numbers:

1. **% of available products fresh** — the headline. If it is falling week over
   week, merchants are not keeping up with the catalog.
2. **Pending requests / overdue** — `overdue` should be `0`. Anything else means
   the sweep is not running, and each overdue row is *blocking a re-prompt* for
   its product.
3. **The red `sweep_freshness is behind` banner** — see below. It should never
   appear.

### The weekly check (5 minutes)

Work the **Needs follow-up** lists. They are three separate lists because they
need three different remedies, and conflating them is how a shop you have never
contacted ends up unverified:

| List | Meaning | What to do |
| --- | --- | --- |
| **Never responds** | Let ≥2 requests expire, answered none | Call them; consider clearing `is_verified` |
| **Unreachable** | No `telegram_chat_id` — we have never been able to ask | Get their chat ID (`/start` echoes it), paste it into the shop record |
| **Never asked** | Reachable, but no request has ever been sent | Nothing, unless prompts are meant to be on |

Then scan the per-shop table for shops carrying a lot of `Stale` products.

### Response rate: what the number means

`answered ÷ (answered + expired)`. Confirmed and sold out both count as answered —
"out of stock" is a real answer and restarts the confirmation clock.

**Pending requests are deliberately excluded from the denominator.** A prompt
sent an hour ago is not a failure to respond; the sweep moves it to `expired`
when the window closes, and it counts against the shop then.

A shop with no resolved requests shows **"never asked", not 0%**. That
distinction is the whole point of the column while
`ENABLE_TELEGRAM_BOT_PROMPTS` is off: there are no requests at all, so a naive
`answered ÷ total` would render every shop at 0% and propose unverifying the
entire marketplace on day one.

### Sweep drift — the "is anything running?" check

`confirmation_status` is a stored column written **only** by `sweep_freshness`.
The page reports that column, because it is what buyers receive as the confirmation
status. It also recomputes the same predicate against the live clock and shows the gap:

- `stale` — what the database says today.
- `stale_by_clock` — what a sweep right now would produce.
- `sweep_drift` — the difference. **Non-zero means the sweep is behind.**

This matters because a sweep that silently stopped running looks *identical to a
perfectly healthy catalog*: `confirmation_status` freezes, and everything keeps
reporting fresh forever. Drift is the only signal that separates the two.

### Reconciling against seeded data

```bash
docker compose exec backend python manage.py seed_shop_data
docker compose exec backend python manage.py sweep_freshness
```

Expected on a clean seed is pinned by `ops/tests.py::SeedReconciliationTests` —
the exact counts depend on `CONFIRMATION_INTERVAL_DAYS`. The shape is what matters:
before the first sweep the page shows **`0` stale** (the seeder writes
`last_confirmed_at` but never touches `confirmation_status`, and the page reports
what buyers actually get), while `stale_by_clock` shows what a sweep right now
would produce. The gap between them is `sweep_drift`.

**Before the sweep the page shows 0 stale, not 3.** That is correct, not a bug:
the seeder writes `last_confirmed_at` but never touches `confirmation_status`,
and the page reports what buyers actually get. `sweep_drift: 3` is what tells
you the two disagree.

Seeded shops carry no `telegram_chat_id`, so all three land in **Unreachable**
and every response rate reads "never asked". On a freshly seeded database that
is the honest reading: the loop has never run. See *Two switches, and what each
one turns off* above for what it takes to change that.

## Migration workflow

- **Generate:** `docker compose exec backend python manage.py makemigrations <app>`
- **Check for drift:** `docker compose exec backend python manage.py makemigrations --check --dry-run`
- **Apply:** `docker compose exec backend python manage.py migrate`
- A migration must apply cleanly on a fresh database.

## Project structure

```
config/            project config (settings, urls, wsgi/asgi)
core/              shared utilities (BaseModel, pagination)
health/            liveness + database check
users/             custom User model
shops/             Shop domain (CRUD API)
catalog/           categories + products (read-only API)
bot/               Telegram bot: digest, listing management, /menu, run_bot + Celery tasks
ops/               internal loop-health page + endpoint (no models)
tests/             project-level tests
```

## Testing

- pytest is configured but is a **dev-only dependency** (not installed in the
  production image). Inside the container, use Django's runner:

  ```bash
  docker compose exec backend python manage.py test <app> -v 2
  ```

- To use pytest locally, install `requirements-dev.txt` in a venv on the host.
- Every model/feature change must include at least one test.

## Troubleshooting

- **Port 5432 already in use** — a native Postgres is running on your host.
  This project maps Postgres to host port **5434** to avoid the clash; your app
  connects internally on `db:5432`, so no config change is needed. To connect a
  GUI to the Docker database, use `localhost:5434`.
- **Files created by the container are owned by `root`** — run
  `sudo chown -R $USER:$USER .` from the host to fix.
- **`celery-beat` restarts with `failed to resolve host 'db'`** — a transient
  container-DNS blip (usually during recreate) made beat lose Postgres. The
  compose files pin `db`/`redis` to static IPs (`172.30.0.10`/`.11`) and give the
  app services `/etc/hosts` entries, so this no longer reaches them. If you change
  the subnet, update the `extra_hosts` anchors to match.
