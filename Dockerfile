# Recipe for the app's container. Multi-stage: shared base, then dev + prod.
# Local runs the `dev` target; production runs `prod`. Same base = parity.

FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential libpq-dev && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ---- dev: code is mounted by compose for hot-reload ----
FROM base AS dev
CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]

# ---- prod: code baked in, gunicorn serves it (used in Go Live) ----
FROM base AS prod
COPY . .
EXPOSE 8000
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "3"]
