# Tinsae Backend

Backend API for **Tinsae** — a local product-discovery marketplace.
Django 5.1 + Django REST Framework + PostgreSQL, fully containerized.

## Architecture overview

The project follows Django's **app-per-domain** pattern. Each app owns one
domain concept and has a clear dependency direction:

```
users ──> shops ──> catalog
  │
  └── provides authentication + ownership
```

### Apps and their responsibilities

| App | Role | Depends on |
|---|---|---|
| `users` | Custom User model, auth, merchant profiles | nothing |
| `shops` | Shop CRUD, merchant storefronts | `users` (owner FK) |
| `catalog` | Categories, products (read-only catalog) | `core` (BaseModel) |
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
