# CLAUDE.md

Guidance for AI coding assistants working in this repository. `CLAUDE.md` and
`GEMINI.md` are kept identical — edit both or neither.

## Working agreement (read first)

- **Never run shell commands.** Do not execute Bash, Docker, `manage.py`, `git`,
  `pytest`, migrations, or any other command. Instead, give me the exact command(s)
  to run myself, in a copyable block, and let me report back the output.
- **Propose before applying.** Present code changes in the chat first (diffs or full
  snippets). I will either apply them myself or explicitly tell you to apply. Do not
  edit files until I say so.
- **Don't do everything end-to-end.** Keep changes scoped to what I asked. Surface
  bugs, options, and follow-ups as suggestions — let me decide.
- When a task needs verification (tests, migrations, running the app), write the code
  and hand me the commands to verify. Don't run them for me.

## Assistant behaviour (security & performance)

- Before writing or changing code, check it against the rules in this file.
- When you notice existing code that breaks a rule — even outside the current task —
  **flag it**: `file:line`, the issue, severity (critical / high / medium / low), and
  a suggested fix. Flag; don't fix unless asked.
- **Never weaken a security setting to make something work** (no `AllowAny` to get
  past a 401, no `CORS_ALLOW_ALL_ORIGINS`, no `@csrf_exempt`, no `DEBUG=True` in prod,
  no removed throttle). Explain the safe alternative instead.
- Prefer the current official Django, DRF, simplejwt and Celery docs. When unsure
  about a version-specific detail, say so rather than guess.
- **Read the comments.** This codebase documents *why* at length. Many things that
  look like bugs are deliberate trade-offs written up in place (see "Deliberate
  decisions" below). Don't "fix" a documented decision; raise it instead.
- `AUDIT-REVIEW.md` supersedes `AUDIT.md` wherever they disagree. Both are partly
  stale — verify against current code before citing either.

## Project overview

`tinsae-backend` — API for Tinsae, a verified-shop marketplace (Addis Ababa).
Discovery-first: no cart or checkout. Merchants confirm stock freshness via web
dashboard or Telegram bot.

- Python 3.12 · Django 5.1 · DRF 3.15 · djangorestframework-simplejwt 5.x
- Postgres 16 (required everywhere — no SQLite fallback, dev/prod parity)
- Redis 7 (cache on DB `/1`, Celery broker on `/0`) · Celery 5 + django-celery-beat
- python-telegram-bot 22.8 · Pillow · gunicorn (prod) · django-cors-headers
- Frontend: Next.js app in `../tinsae-frontend` (separate repo)

## Layout

- `config/` — `settings/{base,dev,prod}.py` (base reads `.env` via
  `django-environ`; dev/prod import from base), `urls.py`, `wsgi.py`, `asgi.py`,
  Celery app
- `core/` — shared base model, slugs, pagination (`core.pagination`)
- `users/` — custom `User`, multi-identifier login, OTP, password reset, throttles
- `shops/` — `Shop` (root entity), photos, verification, merchant product endpoints
- `catalog/` — `Product`, `Category`, images, search, facets, freshness services
- `bot/` — Telegram poller, outbox, link codes (a delivery channel *below*
  `catalog`/`shops`)
- `ops/` — staff-only health metrics (API + HTML page)
- `health/` — public health check
- `tests/` + per-app tests (`tests.py`, `tests_*.py`, `catalog/tests/`)
- `config/settings/perf.py` — load-test settings (throttles off, `ALLOWED_HOSTS=*`);
  **never deploy it**. `load-tests/` — k6 scenarios and reports
- `docker-compose.prod.yml` + `ops/nginx/prod.conf` — prod stack (Hetzner): nginx
  serves `/static/` and `/media/` from volumes and proxies the rest to gunicorn
- `.github/workflows/backend-ci.yml` — ruff, black, `makemigrations --check`, pytest

## Local dev (commands for me to run)

Services: `db` (5434→5432), `redis` (6380→6379), `backend` (8000),
`celery-worker`, `celery-beat`, `bot`.

```bash
docker compose up                                   # whole stack; backend auto-migrates
docker compose exec backend python manage.py <cmd>  # management commands
docker compose exec backend python manage.py test <app> -v 2   # tests (pytest is NOT in the image)
docker compose exec backend python manage.py makemigrations --check --dry-run
DJANGO_SETTINGS_MODULE=config.settings.prod docker compose exec backend python manage.py check --deploy
```

Lint/format run on the host in a venv with `requirements-dev.txt`:
`black --check .` and `ruff check .`

## Code style & conventions

- black (88, py312); ruff `E, F, I, B, C4, UP`, ignore `E501`. Match the file.
- Comment the *why*, not the *what* — keep the existing register; don't strip comments.
- Every model/feature change ships with at least one test.
- Branches `TIN-<ticket>-<short-desc>`; commits `feat:` / `fix:` / `chore:`.
- Register new models in admin with `list_display`, `list_filter`, `search_fields`.
- Business logic lives in `services.py`; views stay thin. State transitions on the
  confirmation clock go through `catalog.services` only.

## Auth flow (shared with the frontend — keep both repos' docs in sync)

1. Browser talks to **Next.js route handlers**, never to Django's auth endpoints.
2. Next's server calls `POST /api/v1/auth/token/`, `/register/`, `/otp/verify/`,
   `/token/refresh/`, `/logout/`, `GET /me/` using `API_INTERNAL_URL`.
3. Django returns a simplejwt pair: access 15 min, refresh 14 days, rotated on
   every refresh and the old one blacklisted.
4. Next stores both in `httpOnly`, `SameSite=Lax`, `Secure` + `__Host-`-prefixed
   (prod) cookies. Tokens never reach client JS.
5. Server Components call Django with `Authorization: Bearer <access>`.
   `src/proxy.ts` refreshes silently when the access cookie has expired.
6. Anonymous catalogue reads may go browser → Django directly
   (`NEXT_PUBLIC_API_URL`), which is why CORS exists.
7. Django API auth is JWT-header only, so CSRF does not apply to it. Session auth
   exists only for `/admin/` and `ops`.

## Rules — Django security

**Secrets & settings**
- `SECRET_KEY`, DB URL, bot token, SMTP and storage credentials come only from env
  (`env(...)` with no default for anything secret). Never hardcode or commit them;
  `.env` is git-ignored, `.env.example` holds placeholders only.
- `config/settings/prod.py` forces `DEBUG = False` and requires `DJANGO_ALLOWED_HOSTS`.
  Keep `SECURE_SSL_REDIRECT`, `SESSION_COOKIE_SECURE`, `CSRF_COOKIE_SECURE`, HSTS
  (+ subdomains/preload), `SECURE_PROXY_SSL_HEADER`, `SECURE_CONTENT_TYPE_NOSNIFF`,
  `X_FRAME_OPTIONS = "DENY"` (default), `SECURE_REFERRER_POLICY` (default
  `same-origin`). `manage.py check --deploy` must pass with prod settings.
- `CSRF_TRUSTED_ORIGINS` and `CORS_ALLOWED_ORIGINS` are explicit, scheme-qualified
  lists of the real frontend/admin origins. Never `CORS_ALLOW_ALL_ORIGINS = True`
  or `CORS_ORIGIN_REGEX` wildcards.
- Passwords: Argon2 first in `PASSWORD_HASHERS`; keep all four
  `AUTH_PASSWORD_VALIDATORS`. DRF doesn't call validators itself — use
  `users.serializers.run_password_policy`.

**DRF**
- Every view declares `permission_classes` explicitly. Write endpoints require
  `IsAuthenticated` + an ownership check. Use `AllowAny` only for genuinely public
  reads or pre-auth flows, with a comment saying why.
- Credential, OTP, registration, reset, upload and other costly endpoints carry a
  throttle (`throttle_scope` or `throttle_classes`). Remember the default is
  `ScopedRateThrottle`: **a view with no scope is unthrottled.** Setting
  `throttle_classes` replaces the default, so a `throttle_scope` beside it is dead.
- Every list endpoint is paginated (`core.pagination`); never return an unbounded
  queryset. `pagination_class = None` only for small, bounded lists, with a comment.
- All input goes through serializers. Write serializers list `fields` explicitly —
  never `"__all__"` — and mark server-owned fields (`owner`, `shop`, `slug`,
  `is_verified`, `telegram_chat_id`, the confirmation clock) in `read_only_fields`.
- Allowlist `ordering` and filter params (see `ORDERING_FIELDS`, `price_param`);
  convert parse errors to DRF `ValidationError` so they're a 400, not a 500.

**Authorization (IDOR)**
- Scope querysets to the caller: filter by `owner=request.user` /
  `shop__owner=request.user` in `get_queryset()`/`get_object()`, so someone else's
  object is a **404**, not a 403. Never fetch by id/slug and check afterwards.
- Never accept an owning FK (`shop`, `product`, `owner`) from the request body; take
  it from the ownership-checked URL.

**Auth & tokens**
- Keep simplejwt short access / rotating + blacklisted refresh. Revoke all refresh
  tokens on password change/reset (`revoke_all_refresh_tokens`).
- Pre-auth endpoints must not reveal whether an account exists (same status, body
  and roughly the same timing). Send emails from Celery, not in-request.
- Compare secrets with `secrets.compare_digest`/`hmac.compare_digest`. Lock and
  count attempts inside `transaction.atomic()` + `select_for_update()`, and raise
  **after** the block (a raise inside rolls back the counter).
- `NUM_PROXIES` must equal the real hop count in front of Django, or per-IP
  throttles become forgeable or shared.

**Injection & unsafe APIs**
- ORM or parameterised queries only — no f-string/`%` SQL, no `.extra()`, and `RawSQL`
  only with `params`. No `eval`/`exec`, no `pickle`/`yaml.load` on untrusted data, no
  `subprocess(..., shell=True)`, no `mark_safe` on user content.

**Uploads**
- Validate size, real format (Pillow, not extension), and pixel dimensions —
  reuse `catalog.image_utils.validate_image_file`. Cap counts per owner.
- Prod media is served by nginx from a volume (never by Django, never executable),
  with `X-Content-Type-Options: nosniff`. Prefer a separate media domain or object
  storage as it grows. Delete files when their row/field is replaced or deleted.

**Logging & errors**
- Never log passwords, tokens, OTP codes, link codes, full phone numbers or emails.
  Log user pks instead. `dev_code` may only appear when `DEBUG`.
- Errors use DRF's shape (`{"detail": ...}` or `{"field": [...]}`). Never return
  `str(exc)`, tracebacks or SQL. Existing OTP error strings are matched by the
  frontend — don't reword them.

**Dependencies**
- Every package has an upper bound in `requirements.txt`; dev tools live only in
  `requirements-dev.txt`. Stay on a Django release that still gets security fixes
  (5.2 LTS or newer). CI should run `pip-audit` and `bandit -r . -x tests`.
- Compose files never carry a fallback for a secret: `${POSTGRES_PASSWORD:?}`,
  not `${POSTGRES_PASSWORD:-tinsae}`.

## Rules — Django performance

- Avoid N+1: `select_related` for FKs, `prefetch_related`/`Prefetch` for reverse/M2M.
  Order inside the `Prefetch` queryset, never via `.order_by()` on the prefetched
  manager. **Flag any loop that queries the DB.**
- Use `.exists()` / `.count()` instead of loading rows. Use `.only()`/`.values()`
  when full objects aren't needed. Use `bulk_create`/`bulk_update`/`.update()` for
  batches, but `bulk_update` skips `auto_now` — set `updated_at` yourself — and
  never bypass a documented single writer (e.g. `resolve_request`).
- Cache per-request lookups on `self` (e.g. `get_shop()`), so a view doesn't run the
  same query two or three times.
- Index fields used in filters, ordering, joins and lookups (`db_index=True` or
  `Meta.indexes`). Add a migration for each.
- `CONN_MAX_AGE` stays on (set to 0 only behind transaction-mode pgbouncer). Cache
  expensive or repeated reads in Redis (`cache_page`, low-level cache).
- Emails, image processing, Telegram sends and third-party calls go to Celery
  tasks. Periodic jobs are beat schedules, not ad-hoc cron.
- Bound user-controlled query complexity (word counts, list params, page size ≤ 100).
- Migrations must be safe on a large live table: add columns nullable or with a db
  default, build indexes with `AddIndexConcurrently` (`atomic = False`), split data
  backfills from schema changes, and always give `RunPython` a `reverse_code`.

## Cross-cutting (Django ↔ Next.js)

- `CORS_ALLOWED_ORIGINS`, `CSRF_TRUSTED_ORIGINS`, `ALLOWED_HOSTS`, the frontend's
  `NEXT_PUBLIC_API_URL`/`API_INTERNAL_URL`/`SITE_URL`, and cookie
  `SameSite`/`Domain` must all match the real deployed domains. Change them together.
- Client IP: the Next server forwards `X-Forwarded-For` to Django. Only the
  trusted edge may set it; Django's `NUM_PROXIES` must match the path a request
  actually takes.
- One error shape (DRF's). Never leak stack traces or internals to clients.

## Deliberate decisions (don't flag as violations)

- `DEFAULT_PERMISSION_CLASSES = IsAuthenticatedOrReadOnly`: the catalogue is public.
  Views that write still declare their own permissions.
- `ScopedRateThrottle` default: catalogue browsing is intentionally unthrottled.
- `LocMemCache` fallback when `REDIS_URL` is unset (dev only).
- `catalog` imports `bot.models.ConfirmationRequest` (acyclic, documented).
- ASCII-only slugs with `"shop"`/`"product"` fallback (unicode slugs need URL
  converter + `SlugField` changes).
- Open product questions — leave alone unless asked: `Product.status` vs
  `is_available` (TODO.md §7), `confirm-all` rubber-stamping, OTP enumeration fix.

## Gotchas

- **Files created by the container are owned by `root`** (bind mount). If a file
  can't be edited from the host, flag it to me — don't chown it yourself.
- `TIME_ZONE = "Africa/Addis_Ababa"`, `USE_TZ = True`. Quiet hours are local time.
- All config comes from `.env`; missing required vars fail loudly by design.
- Exactly one `bot` poller per token (Telegram 409s a second `getUpdates`).
- DRF throttle state persists across tests in one process — clear the cache
  in throttle tests.
