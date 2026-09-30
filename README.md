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

## Freshness Sweep (TIN-23 / TIN-24 / TIN-25)

Reconciles `Product.confirmation_status`, expires unanswered confirmation
requests, and prompts shop owners over Telegram to confirm stock.

```bash
# Run manually:
docker compose exec backend python manage.py sweep_freshness

# Simulate — writes nothing, sends nothing:
docker compose exec backend python manage.py sweep_freshness --dry-run
```

The command is idempotent: re-running it changes nothing until the clock moves.
It prints one summary line.

```
[Freshness Sweep Complete] Stale products: 12 | Newly stale: 3 | Restored to fresh: 0 |
Prompts sent: 2 | Expired requests: 1 | Skipped: 10
```

- **Stale products** — total currently past the window.
- **Newly stale / Restored to fresh** — transitions on this run. Both should be `0`
  on a repeat run; anything else means the reconcile is flapping.
- **Skipped** — stale, but not prompted: no `telegram_chat_id`, a prompt already
  pending, or prompts disabled.

### Configuration

Thresholds are settings, not flags, because `confirmation_status` is a stored
column — two runs at different thresholds would leave it disagreeing with itself
and with the buyer-facing freshness status.

| Env var | Default | Meaning |
| --- | --- | --- |
| `CONFIRMATION_INTERVAL_DAYS` | `7` | Days without a signal before a product is stale / due |
| `CONFIRMATION_HIDDEN_AFTER_DAYS` | `14` | Days after which an unconfirmed listing is hidden |
| `CONFIRMATION_REQUEST_EXPIRY_HOURS` | `48` | Hours before an unanswered prompt expires |
| `ENABLE_TELEGRAM_BOT_PROMPTS` | `False` | Master switch for **outbound** prompts |
| `TELEGRAM_BOT_TOKEN` | — | Required to reach Telegram at all |
| `MYPRODUCTS_PAGE_SIZE` | `5` | Products per page in the bot's `/myproducts` listing |

**Prompts need both `ENABLE_TELEGRAM_BOT_PROMPTS=True` and a valid
`TELEGRAM_BOT_TOKEN`.** With the flag off — the default — the sweep still
reconciles statuses and expires requests, and reports every stale product under
`Skipped`. If you see `Prompts sent: 0` and expected otherwise, check the flag
first.

`--staleness-days` and `--expiry-hours` override the settings for a single run.
Useful for inspection; avoid them in anything recurring, since a run at a
different threshold rewrites `confirmation_status` for every product. The sweep
reconciles in both directions, so the next normal run corrects it.

```bash
docker compose exec backend python manage.py sweep_freshness --staleness-days 14
```

### What counts as stale

A product is stale when it is still listed as available **and** either:

- `last_confirmed_at` is older than `CONFIRMATION_INTERVAL_DAYS`, or
- it has never been confirmed at all (`last_confirmed_at` is `NULL`).

A never-confirmed listing is stale from the moment it is created — the age of the
listing is not evidence that its stock claim still holds.

Products with `is_available = False` are outside the sweep entirely: they are
neither marked stale nor restored to fresh, and they are never prompted. **A
merchant who answers "No, out of stock" therefore takes that product out of the
loop permanently** — nothing re-prompts it, so there is no automated path back to
available. A re-stock flow is follow-up work, not part of this command.

### Two switches, and what each one turns off

The bot has an outbound half and an inbound half, and they are controlled
separately. Conflating them is the easiest way to misread this system.

| | Controls | Turned off by |
| --- | --- | --- |
| **Outbound** | `sweep_freshness` sending unsolicited prompts | `ENABLE_TELEGRAM_BOT_PROMPTS=False` (the default) |
| **Inbound** | `/start`, `/help`, `/myproducts`, button callbacks | not running `run_bot` (it also refuses to start without a token) |

`/myproducts` and the button callbacks are **not** gated on
`ENABLE_TELEGRAM_BOT_PROMPTS`. That flag is outbound policy — "should we ping
merchants?" — and a merchant who opened the listing themselves is already talking
to us. Silently ignoring them would not be "disabled", it would be broken.

That makes three sensible configurations:

| Bot process | Prompts flag | What you get |
| --- | --- | --- |
| not running | off | **Current default.** Nothing sends, nothing listens. The API, sweep and admin are unaffected. |
| running | off | No unsolicited pings, but merchants can self-serve via `/myproducts`. Freshness data stays real. |
| running | on | The full loop: the sweep prompts, merchants answer inline or via `/myproducts`. |

### Running with the bot fully off

The default state, and a safe one — but the sweep should stay **unscheduled**
while it lasts.

Nothing runs on its own. The `bot` app just holds an empty `ConfirmationRequest`
table, and the dependency arrow points `bot → catalog`, never back, so the public
API never reaches this code.

**Do not add the cron entry below while the bot is fully off.** With nothing
inbound, nothing writes `last_confirmed_at` — the Telegram callbacks are the only
code paths that do, apart from `seed_shop_data` and manual admin edits. Because a
NULL `last_confirmed_at` counts as stale, a scheduled sweep would walk the whole
catalog to `stale` within `CONFIRMATION_INTERVAL_DAYS` of the last seed, and anything
added through the admin would be stale on creation. A confirmation-status-based filter
would then return an empty list.

Leave it unscheduled and `confirmation_status` stays at its `fresh` default,
and product ranking degrades to plain recency.
Two things follow:

- The frontend should not ship a default-on "fresh only" filter in this state —
  it would be filtering on a column nobody maintains.
- Keep `python-telegram-bot` installed even if the bot is idle. `bot/tests.py`
  imports `run_bot`, so dropping it breaks the test suite, not just the bot.

To make freshness mean something without enabling outbound prompts, run `run_bot`
with the flag left off and point merchants at `/myproducts`. That is the middle
row of the table above.

### Local Cron Scheduling

Only once prompts are enabled — see the section above.
*(Note: `sweep_freshness` is not scheduled on Celery yet — A0 is scaffolding only — so host cron remains the runner for now.)*

To run the sweep daily at midnight on your host machine:

```bash
crontab -e
```

Add the following (update the path to your checkout):

```cron
0 0 * * * cd /path/to/tinsae-backend && /usr/bin/docker compose exec -T backend python manage.py sweep_freshness >> /tmp/freshness_sweep.log 2>&1
```

`-T` is required — cron has no TTY and `docker compose exec` fails without it.
Because the command is idempotent, a missed run costs nothing and a doubled run
is harmless.

### Telegram Bot Listener (`run_bot`)

The single inbound process. Telegram allows only one `getUpdates` consumer per
token, so onboarding (`/start`, `/help`), the `/myproducts` listing, and stock
callbacks all live here.

```bash
docker compose exec backend python manage.py run_bot
```

It long-polls until you stop it, and exits with a `CommandError` if
`TELEGRAM_BOT_TOKEN` is unset.

### `/myproducts` — merchant-initiated confirmation

A merchant sends `/myproducts` and gets one message listing every product of
theirs waiting on an answer, with a ✅ and ❌ button per row:

```
3 products waiting on you (page 1 of 1).
Tap ✅ if it is still available, ❌ if it is out of stock.

[ ✅ Samsung Galaxy A15 ] [ ❌ ]
[ ✅ Tecno Camon 20     ] [ ❌ ]
[ ✅ Nokia 105 Classic  ] [ ❌ ]
```

The point is to answer several products in one sitting instead of waiting for a
separate ping per product. **There is deliberately no "confirm everything"
button** — one tap writing forty fresh timestamps produces confirmations the
merchant never actually thought about, and freshness data that is wrong but looks
right cannot be told apart from the real thing later. Each ✅ stays a per-product
decision.

Details worth knowing:

- **Which products appear** is the same predicate the sweep uses —
  `ProductQuerySet.needing_confirmation()` in `catalog/models.py`. One definition,
  so the bot and `sweep_freshness` cannot drift apart about who needs asking.
- **Ordering** is never-confirmed first, then oldest, with `pk` breaking ties so
  paging cannot drop or repeat a row.
- **Paging** kicks in past `MYPRODUCTS_PAGE_SIZE` rows and edits the existing
  message rather than sending new ones.
- **Scoping** is derived from the inbound chat id only, never from button data —
  otherwise paging would become a way to read another merchant's catalogue. One
  chat may own several shops; all of them are listed, prefixed by shop name.
- **Answering resolves any outstanding prompt** for that product, so the sweep
  will not ask again once the old request expires.
- **✅ on an out-of-stock product is refused.** The listing never offers it, so
  reaching that state means tapping a button in an older message. Honouring it
  would quietly republish the product — a back door into the re-stock path that
  does not exist yet. ❌ is always honoured; re-denying is idempotent.

Callback payloads are namespaced: `pconfirm:<product_pk>:<page>` and
`pdeny:<product_pk>:<page>` for the listing, `page:<n>` for navigation, versus the
older `confirm_<request_pk>` / `deny_<request_pk>` for inline prompts. Telegram
caps `callback_data` at 64 bytes, which is why the target is a single id rather
than a list.

The listener keeps its update offset in memory only. After a restart Telegram may
re-deliver up to 24 hours of queued updates; callbacks against an already-answered
request are rejected rather than reapplied. Every callback — inline prompt or
`/myproducts` button — is checked against the `telegram_chat_id` of the shop that
owns the product, so one merchant cannot answer another's.

### Celery & Redis Background Processing

Celery handles background processing and periodic tasks with Redis as the broker and result backend. Periodic task schedules are stored in the database via `django-celery-beat`.

In Docker Compose, `redis`, `celery-worker`, and `celery-beat` run as services alongside `backend` and `db`:

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

#### Bot Periodic Schedules

To configure periodic schedules in `django-celery-beat` (e.g. running `bot.tasks.drain_outbox` every 15 seconds):

```bash
docker compose exec backend python manage.py setup_bot_schedules
```

#### Verifying Celery Locally


1. **Direct smoke task execution**:
   ```bash
   docker compose exec backend python manage.py shell -c "from bot.tasks import smoke_heartbeat; res = smoke_heartbeat.delay(); print('Result:', res.get(timeout=5))"
   ```
2. **PeriodicTask schedule (manual verification)**:
   - Navigate to Django Admin at `http://localhost:8000/admin/django_celery_beat/periodictask/`.
   - Add a test periodic task pointing to `bot.tasks.smoke_heartbeat` with an interval.
   - Watch `docker compose logs -f celery-worker` to see the task executed by the scheduler.
   - Delete the test task after verifying (do not commit test periodic tasks to code/migrations).


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

Expected on a clean seed — pinned by `ops/tests.py::SeedReconciliationTests`:

| | Before sweep | After sweep |
| --- | --- | --- |
| Available products | 31 | 31 |
| Fresh | 31 | 28 (90.3%) |
| Stale | 0 | 3 |
| Stale by clock | 3 | 3 |
| Sweep drift | 3 | 0 |
| Confirmation requests | 0 | 0 |

The three stale products are the ones the seeder labels `expired` (Nokia 105
Classic, Mi Band 8, Nokia G22) at 45 days, past the 30-day window. `almost_expired`
is 25 days and stays fresh.

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
bot/               Telegram prompts, /myproducts, sweep_freshness + run_bot
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
