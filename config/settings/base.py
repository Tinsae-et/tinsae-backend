from pathlib import Path

import environ

BASE_DIR = Path(__file__).resolve().parent.parent.parent
env = environ.Env()
environ.Env.read_env(BASE_DIR / ".env")

SECRET_KEY = env("DJANGO_SECRET_KEY")
DEBUG = env.bool("DJANGO_DEBUG", default=False)
ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS", default=["localhost", "127.0.0.1"])
TELEGRAM_BOT_TOKEN = env("TELEGRAM_BOT_TOKEN", default="")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "corsheaders",
    "health",
    "users",
    "shops",
    "core",
    "catalog",
    "bot",
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

AUTH_USER_MODEL = "users.User"

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
    "DEFAULT_PERMISSION_CLASSES": (
        "rest_framework.permissions.IsAuthenticatedOrReadOnly",
    ),
    "DEFAULT_AUTHENTICATION_CLASSES": (
        "rest_framework_simplejwt.authentication.JWTAuthentication",
    ),
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

# Days without a confirmation signal before a product is considered stale.
# This lives in settings rather than a CLI flag because Product.confirmation_status
# is a materialised column: two sweeps run with different thresholds would leave
# the column disagreeing with itself and with the API's freshness filter.
PRODUCT_STALENESS_DAYS = env.int("PRODUCT_STALENESS_DAYS", default=30)

# Hours a ConfirmationRequest may sit unanswered before the sweep expires it and
# allows a fresh prompt to be sent.
CONFIRMATION_REQUEST_EXPIRY_HOURS = env.int(
    "CONFIRMATION_REQUEST_EXPIRY_HOURS", default=48
)

# Products per page in the bot's /myproducts listing. Telegram inline keyboards
# become unusable past a handful of rows, so the listing pages instead of capping.
MYPRODUCTS_PAGE_SIZE = env.int("MYPRODUCTS_PAGE_SIZE", default=5)

CELERY_BEAT_SCHEDULE = {
    "expire-confirmation-requests": {
        "task": "catalog.tasks.expire_confirmation_requests_task",
        "schedule": 30 * 60,
    },
}
