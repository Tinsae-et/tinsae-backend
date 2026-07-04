# tinsae-backend

Backend API for **Tinsae** — a local product-discovery marketplace.
Django 5 + Django REST Framework, PostgreSQL, fully containerized with Docker.

## Prerequisites

You only need these installed on your machine:

- **Docker** (with the Compose v2 plugin — `docker compose version` should work)
- **Git**

Python and PostgreSQL run **inside containers** — you do not install them on your host.

## Getting started

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

## How it runs

`docker compose up` starts two containers:

- **`db`** — PostgreSQL 16, the database.
- **`backend`** — the Django app (Python 3.12). On start it runs migrations, then the dev server.

Your code is mounted into the `backend` container, so **editing a `.py` file auto-reloads** the server — no rebuild needed. You only rebuild (`docker compose up --build`) when dependencies change (`requirements.txt`) or the Docker setup changes.

## Configuration

All settings come from environment variables (see `.env.example`). The real `.env` is git-ignored and must never be committed.

`DATABASE_URL` is **required** — the app fails loud if it's missing (no silent SQLite fallback), so dev and production always use the same database engine.

## Settings layout

Settings are split so dev and production share a common base and differ only where they must:
config/settings/
├── base.py   # shared by all environments
├── dev.py    # local development (DEBUG on)
└── prod.py   # production (DEBUG off, security hardened) — used in Go Live

Django defaults to `config.settings.dev` locally.

## Project structure
config/            project config (settings, urls, wsgi/asgi)
health/            /api/health/ liveness + database check
Dockerfile         multi-stage image (dev + prod targets)
docker-compose.yml Postgres + backend for local development
requirements.txt   Python dependencies
.env.example       environment variable template

## Common commands

```bash
docker compose up                 # start the stack
docker compose up --build         # rebuild (after dependency changes)
docker compose down               # stop and remove containers
docker compose logs -f backend    # tail the app logs
docker compose exec backend python manage.py migrate        # run migrations
docker compose exec backend python manage.py createsuperuser # create admin user
```

## Troubleshooting

- **Port 5432 already in use** — a native Postgres is running on your host.
  This project maps Postgres to host port **5433** to avoid the clash; your app
  connects internally on `db:5432`, so no config change is needed. To connect a
  GUI to the Docker database, use `localhost:5433`.

## Conventions

See the team's **Engineering Conventions** doc in Notion. In short: trunk-based
branches named `bac-<id>-<summary>`, Conventional Commits, and every PR links
its Linear issue with `Closes BAC-XX`.
