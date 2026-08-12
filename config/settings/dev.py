from .base import *  # noqa: F401,F403
from .base import env

DEBUG = env.bool("DJANGO_DEBUG", default=True)
# "backend" = the compose service name; "host.docker.internal" = the frontend
# container reaching the host gateway during local dev (extra_hosts mapping).
ALLOWED_HOSTS = ["localhost", "127.0.0.1", "0.0.0.0", "backend", "host.docker.internal"]
