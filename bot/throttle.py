"""Rate limiting and attempt throttling for Telegram bot interactions.

Note:
The failed attempt counter is only correct across workers when a shared cache
(Redis) is configured via REDIS_URL. The LocMemCache fallback is per-worker,
meaning effective limits multiply by the worker count in that setup.
"""

from django.conf import settings
from django.core.cache import cache


def _link_failure_key(chat_id: str | int) -> str:
    return f"linkfail:{chat_id}"


def link_attempts_exceeded(chat_id: str | int | None) -> bool:
    """Return True if failed link attempts for chat_id reach or exceed the limit."""
    if chat_id in (None, ""):
        return False
    limit = getattr(settings, "TELEGRAM_LINK_FAILURE_LIMIT", 5)
    failures = cache.get(_link_failure_key(chat_id), 0)
    return failures >= limit


def record_link_failure(chat_id: str | int | None) -> None:
    """Increment the failed link attempt count for chat_id, expiring after the configured window."""
    if chat_id in (None, ""):
        return
    key = _link_failure_key(chat_id)
    window = getattr(settings, "TELEGRAM_LINK_FAILURE_WINDOW_SECONDS", 3600)
    # cache.add only sets the key if it doesn't already exist.
    added = cache.add(key, 1, timeout=window)
    if not added:
        try:
            cache.incr(key)
        except ValueError:
            # Key may have expired between add and incr
            cache.set(key, 1, timeout=window)


def clear_link_failures(chat_id: str | int | None) -> None:
    """Clear failed link attempt counter for chat_id after successful authentication."""
    if chat_id in (None, ""):
        return
    cache.delete(_link_failure_key(chat_id))
