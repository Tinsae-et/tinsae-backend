# Recipe for the app's container. Multi-stage: shared base, then dev + prod.
# Local runs the `dev` target; production runs `prod`. Same base = parity.

FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential libpq-dev && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Both stages pin the settings module explicitly. manage.py, wsgi.py, asgi.py and
# conftest.py all `setdefault` to config.settings.dev, so an unset variable is not
# an error -- it is dev settings serving production traffic with DEBUG on. The dev
# line is redundant on its own and kept for the symmetry: it is what makes the
# prod line read as deliberate rather than stray.

# ---- dev: code is mounted by compose for hot-reload ----
FROM base AS dev
ENV DJANGO_SETTINGS_MODULE=config.settings.dev
CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]

# ---- prod: code baked in, gunicorn serves it (used in Go Live) ----
FROM base AS prod
ENV DJANGO_SETTINGS_MODULE=config.settings.prod
COPY . .
EXPOSE 8000
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "3"]
