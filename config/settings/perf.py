"""Performance and load-testing settings.

Inherits from base settings but disables rate limiting / throttling so k6 synthetic
traffic measures true server capacity rather than being rejected with HTTP 429.
Runs with DEBUG=False to measure real production-like throughput and query behavior.
"""

from .base import *  # noqa: F401,F403
from .base import REST_FRAMEWORK

DEBUG = False

# Allow local benchmark traffic from host, docker network, or test runners
ALLOWED_HOSTS = ["*"]

# Disable DRF throttling for performance testing runs by setting astronomical rates
REST_FRAMEWORK = {
    **REST_FRAMEWORK,
    "DEFAULT_THROTTLE_CLASSES": (),
    "DEFAULT_THROTTLE_RATES": {
        "register": "100000/sec",
        "login_ip": "100000/sec",
        "login_account": "100000/sec",
        "token_refresh": "100000/sec",
        "password_reset": "100000/sec",
        "profile_update": "100000/sec",
        "public_stats": "100000/sec",
        "otp_verify": "100000/sec",
    },
}

# Keep persistent connections open across requests to mirror production pool/keepalive
CONN_MAX_AGE = 60

# Prevent logging I/O from becoming an artificial bottleneck during heavy load runs
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
        },
    },
    "root": {
        "handlers": ["console"],
        "level": "WARNING",
    },
    "loggers": {
        "django": {
            "handlers": ["console"],
            "level": "WARNING",
            "propagate": False,
        },
        "django.request": {
            "handlers": ["console"],
            "level": "ERROR",
            "propagate": False,
        },
        "catalog": {
            "handlers": ["console"],
            "level": "WARNING",
            "propagate": False,
        },
        "ops": {
            "handlers": ["console"],
            "level": "WARNING",
            "propagate": False,
        },
    },
}
