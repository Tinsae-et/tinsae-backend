from datetime import timedelta
from pathlib import Path

import environ

BASE_DIR = Path(__file__).resolve().parent.parent.parent
env = environ.Env()
environ.Env.read_env(BASE_DIR / ".env")

SECRET_KEY = env("DJANGO_SECRET_KEY")
DEBUG = env.bool("DJANGO_DEBUG", default=False)
ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS", default=["localhost", "127.0.0.1"])
TELEGRAM_BOT_TOKEN = env("TELEGRAM_BOT_TOKEN", default="")
TELEGRAM_BOT_USERNAME = env("TELEGRAM_BOT_USERNAME", default="").lstrip("@")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.postgres",
    "django.contrib.staticfiles",
    "rest_framework",
    "rest_framework_simplejwt.token_blacklist",
    "corsheaders",
    "django_celery_beat",
    "health",
    "users",
    "shops",
    "core",
    "catalog",
    "bot",
    "ops",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ]
        },
    }
]

# Postgres required everywhere — no SQLite fallback (dev/prod parity). Fails loud if unset.
DATABASES = {"default": env.db("DATABASE_URL")}
# Persistent connections. Django's default of 0 opens and closes a connection per
# request, which is the single cheapest latency win available here. Set to 0 if a
# transaction-mode pooler (pgbouncer) is ever put in front of the database —
# persistent connections and transaction pooling are mutually exclusive.
DATABASES["default"]["CONN_MAX_AGE"] = env.int("CONN_MAX_AGE", default=60)

AUTH_USER_MODEL = "users.User"

# Replaces ModelBackend rather than sitting alongside it: MultiIdentifierBackend
# subclasses it and handles the plain `username=` kwarg, so the admin login form
# keeps working and admins gain email/phone login too. Listing both would only
# mean every failed login runs the password hasher twice.
AUTHENTICATION_BACKENDS = ["users.backends.MultiIdentifierBackend"]

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"
    },
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "Africa/Addis_Ababa"
USE_I18N = True
USE_TZ = True
STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

REST_FRAMEWORK = {
    "DEFAULT_PAGINATION_CLASS": "core.pagination.DefaultPagination",
    "PAGE_SIZE": 20,
    # How many proxies sit in front of the app. Load-bearing for the login
    # throttles below, which key on DRF's `get_ident()`: left unset, DRF trusts
    # X-Forwarded-For and takes the client-supplied end of the chain, so a
    # caller picks their own throttle bucket by sending the header and the
    # per-IP cap stops meaning anything. Counting back a fixed number of hops
    # instead makes the value forgeable only by the proxies themselves.
    #
    # 0 locally and in CI (nothing in front, so REMOTE_ADDR is the client); set
    # it to the real hop count in production -- 1 behind a single Railway or
    # nginx layer. Too high is as wrong as too low: it reads an address the
    # client controls.
    "NUM_PROXIES": env.int("NUM_PROXIES", default=0),
    "DEFAULT_PERMISSION_CLASSES": (
        "rest_framework.permissions.IsAuthenticatedOrReadOnly",
    ),
    "DEFAULT_AUTHENTICATION_CLASSES": (
        "rest_framework_simplejwt.authentication.JWTAuthentication",
    ),
    # Scoped rather than anon/user: only views that declare `throttle_scope`
    # are limited, so adding this default cannot silently rate-limit the
    # catalogue browsing endpoints.
    "DEFAULT_THROTTLE_CLASSES": ("rest_framework.throttling.ScopedRateThrottle",),
    "DEFAULT_THROTTLE_RATES": {
        # Registration is the spam target, not login: it needs no credentials
        # and writes two rows per call, so it is deliberately tighter.
        "register": "10/hour",
        # Login is limited on two axes at once (users.throttles). A single
        # per-IP cap is wrong in both directions: it locks out a NAT'd carrier
        # block on legitimate traffic while leaving an attacker who spreads one
        # account's guesses across many IPs unlimited.
        "login_ip": "30/min",
        "login_account": "5/min",
        # A well-behaved client refreshes ~4x/hour at a 15-minute access
        # lifetime. This is loose enough for a shared egress IP and still caps
        # grinding the endpoint.
        "token_refresh": "60/min",
        # Each request sends an email. Tight on purpose.
        "password_reset": "5/hour",
        # Profile writes: identity-adjacent and each one may carry a 2 MB
        # upload. Loose enough that nobody editing their own page reaches it.
        "profile_update": "20/hour",
        "public_stats": "60/min",
        # OTP verification. OTP_MAX_ATTEMPTS caps guesses against one *code*;
        # this caps guesses against one *destination* across successive codes,
        # which is what an attacker actually grinds.
        "otp_verify": "20/hour",
    },
}

SIMPLE_JWT = {
    # 5 minutes (the library default) means a refresh every few clicks. 15 is
    # long enough to be invisible and short enough to bound the window in which
    # a leaked access token — or one belonging to a session that has since
    # logged out — still works.
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=15),
    # The default is 1 day, i.e. merchants log in again every morning.
    "REFRESH_TOKEN_LIFETIME": timedelta(days=14),
    # Together these make a refresh single-use: each one issues a new token and
    # blacklists the one it consumed, so replaying a stolen refresh fails and is
    # visible in the blacklist table.
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": True,
    # Off by default, which leaves `last_login` null forever — the admin and
    # ops/metrics cannot then tell an active merchant from a dormant one.
    "UPDATE_LAST_LOGIN": True,
}

# Console backend by default so password reset is fully exercisable in dev with
# no SMTP anywhere. Unlike DJANGO_SECRET_KEY and DATABASE_URL these do not fail
# loudly when unset: a missing mail server should not stop the server booting.
EMAIL_BACKEND = env(
    "DJANGO_EMAIL_BACKEND", default="django.core.mail.backends.console.EmailBackend"
)
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", default="Tinsae <no-reply@tinsae.et>")

# Password reset links point at the Next.js reset page, not at Django — the
# backend never renders a page for this flow, it only validates the uid/token
# pair the frontend posts back.
FRONTEND_URL = env("FRONTEND_URL", default="http://localhost:3000")
PASSWORD_RESET_TIMEOUT = env.int("PASSWORD_RESET_TIMEOUT", default=60 * 60 * 3)

REDIS_URL = env("REDIS_URL", default="")

# Throttle counters and cache-backed state live here.
# Under multi-worker gunicorn, LocMemCache is per-process so every worker keeps
# its own counter and the effective rate becomes N x the limits. When REDIS_URL
# is configured (e.g. in multi-worker environments), Django's built-in RedisCache
# backend is used so counters are shared across all workers. Use Redis DB /1
# (the Celery broker uses /0) so cache keys cannot collide with the broker.
# If REDIS_URL is not set, LocMemCache serves as the per-process dev fallback.
#
# Both rates key on client IP for anonymous callers. Ethiopian mobile carriers
# NAT aggressively, so a shared egress IP can exhaust these on legitimate
# traffic; widen them if that shows up before the Redis switch.
if REDIS_URL:
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.redis.RedisCache",
            "LOCATION": REDIS_URL,
        }
    }
else:
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "tinsae-throttle",
        }
    }
CORS_ALLOWED_ORIGINS = env.list(
    "CORS_ALLOWED_ORIGINS", default=["http://localhost:3000"]
)
MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"
STORAGES = {
    "default": {
        "BACKEND": env(
            "DEFAULT_FILE_STORAGE",
            default="django.core.files.storage.FileSystemStorage",
        ),
    },
    "staticfiles": {
        "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
    },
}

# TELEGRAM_BOT_TOKEN is defined near the top of this file — it is capability
# ("can we reach Telegram"). The flag below is policy ("should we prompt at all").
# Sending a prompt requires both.
ENABLE_TELEGRAM_BOT_PROMPTS = env.bool("ENABLE_TELEGRAM_BOT_PROMPTS", default=False)
TELEGRAM_MOCK_SEND = env.bool("TELEGRAM_MOCK_SEND", default=False)


# Weekly confirmation digest interval in days, and period after which unconfirmed
# listings are hidden from buyer-facing views. These are the single confirmation
# clock: a request's own `expires_at` is derived from them.
CONFIRMATION_INTERVAL_DAYS = env.int("CONFIRMATION_INTERVAL_DAYS", default=7)
CONFIRMATION_HIDDEN_AFTER_DAYS = env.int("CONFIRMATION_HIDDEN_AFTER_DAYS", default=14)

# Days a SearchLog row is kept before `sweep_search_logs` deletes it.
#
# These rows are search terms attached to user accounts, so they are personal
# data with no expiry of their own -- the table only grows. 180 days is chosen
# to be longer than the tuning loop needs (a few weeks of traffic is enough to
# rank synonym gaps) and short enough that we are not holding a year of buyers'
# queries to no purpose.
#
# Deleting a row does not lose the lesson it taught: the output of this table is
# aggregate counts of failed queries, which get acted on by adding synonyms and
# aliases. Once that edit is made, the individual row is spent.
SEARCH_LOG_RETENTION_DAYS = env.int("SEARCH_LOG_RETENTION_DAYS", default=180)

# Products per page in the bot's /myproducts listing. Telegram inline keyboards
# become unusable past a handful of rows, so the listing pages instead of capping.
MYPRODUCTS_PAGE_SIZE = env.int("MYPRODUCTS_PAGE_SIZE", default=8)

# Hours an admin-issued Telegram link code stays valid. Short on purpose: the
# code is a bearer credential for a shop's prompt channel, and it reaches the
# merchant over whatever channel support happens to be using that day.
TELEGRAM_LINK_CODE_TTL_HOURS = env.int("TELEGRAM_LINK_CODE_TTL_HOURS", default=24)

# Failed link attempt throttle: max failures permitted per chat before
# linking is throttled, and the window (in seconds) the failure counter is retained.
TELEGRAM_LINK_FAILURE_LIMIT = env.int("TELEGRAM_LINK_FAILURE_LIMIT", default=5)
TELEGRAM_LINK_FAILURE_WINDOW_SECONDS = env.int(
    "TELEGRAM_LINK_FAILURE_WINDOW_SECONDS", default=3600
)

# Celery & background tasks
CELERY_BROKER_URL = env("CELERY_BROKER_URL", default="redis://redis:6379/0")
CELERY_RESULT_BACKEND = env("CELERY_RESULT_BACKEND", default="redis://redis:6379/0")
CELERY_TIMEZONE = TIME_ZONE
CELERY_BEAT_SCHEDULER = "django_celery_beat.schedulers:DatabaseScheduler"

# Quiet hours during which proactive Telegram sends are paused.
# Specified in local hours (TIME_ZONE = Africa/Addis_Ababa).
QUIET_HOURS_START = env.int("QUIET_HOURS_START", default=21)
QUIET_HOURS_END = env.int("QUIET_HOURS_END", default=7)

# Pruning retention periods
PROCESSED_UPDATE_RETENTION_DAYS = env.int("PROCESSED_UPDATE_RETENTION_DAYS", default=7)
OUTBOX_RETENTION_DAYS = env.int("OUTBOX_RETENTION_DAYS", default=30)
