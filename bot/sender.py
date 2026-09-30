"""Outbox drainer and rate-limited sender for proactive Telegram messages.

Employs a claim-then-send pattern:
1. Reclaim any rows stuck in 'sending' due to worker crashes.
2. In a short transaction, claim up to 25 'queued' rows using select_for_update(skip_locked=True)
   and mark them 'sending'.
3. Outside the transaction, send each row, respecting rate limits and handling
   Telegram error classifications.
"""

import logging
import time
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from shops.models import Shop

from . import telegram_api
from .models import ConfirmationDigest, ConfirmationRequest, OutboundMessage

logger = logging.getLogger(__name__)

# Backoff intervals for network errors: 15s, 1m, 5m, 30m, 2h. Dead on > 5th attempt.
NETWORK_BACKOFF_SECONDS = [15, 60, 300, 1800, 7200]
MAX_NETWORK_ATTEMPTS = len(NETWORK_BACKOFF_SECONDS)
RECLAIM_TIMEOUT_MINUTES = 5
BATCH_SIZE = 25

# In-process rate limiting tracking.
# Note: These limiters are per-worker process. If multiple workers drain concurrently,
# each worker enforces its own pacing.
_last_global_send_time = 0.0
_last_chat_send_time: dict[str, float] = {}


def _throttle(chat_id: str, rate_limit: bool = True) -> None:
    """Enforce per-chat (<= 1 msg/s) and global (<= 25 msg/s) rate limits."""
    if not rate_limit:
        return

    global _last_global_send_time

    current_time = time.monotonic()

    # Global pacing: 1 / 25s = 0.04s
    elapsed_global = current_time - _last_global_send_time
    if elapsed_global < 0.04:
        time.sleep(0.04 - elapsed_global)

    current_time = time.monotonic()

    # Per-chat pacing: 1.0s
    last_chat = _last_chat_send_time.get(chat_id, 0.0)
    elapsed_chat = current_time - last_chat
    if elapsed_chat < 1.0:
        time.sleep(1.0 - elapsed_chat)

    now = time.monotonic()
    _last_global_send_time = now
    _last_chat_send_time[chat_id] = now

    # Drop stale entries so the map does not grow without bound in a long-running
    # worker. 60s is well past the 1 msg/s per-chat window.
    for stale_chat in [c for c, ts in _last_chat_send_time.items() if now - ts > 60]:
        del _last_chat_send_time[stale_chat]


def is_quiet_hours(now=None) -> bool:
    """Return True if local time falls within configured quiet hours.

    Quiet hours default to 21:00-07:00 Africa/Addis_Ababa (QUIET_HOURS_START to QUIET_HOURS_END).
    """
    if now is None:
        now = timezone.now()
    elif timezone.is_naive(now):
        now = timezone.make_aware(now, timezone.get_current_timezone())
    local_now = timezone.localtime(now)
    hour = local_now.hour

    start = getattr(settings, "QUIET_HOURS_START", 21)
    end = getattr(settings, "QUIET_HOURS_END", 7)

    if start > end:
        return hour >= start or hour < end
    elif start < end:
        return start <= hour < end
    return False


def drain_outbox(
    *,
    batch_size: int = BATCH_SIZE,
    rate_limit: bool = True,
    respect_quiet_hours: bool = True,
) -> int:
    """Drain due queued outbound messages and send them via Telegram.

    Returns the number of messages processed in this batch.
    """
    if respect_quiet_hours and is_quiet_hours():
        logger.info("Quiet hours active; outbox draining deferred.")
        return 0

    now = timezone.now()

    # 1. Reclaim: rows stuck in status="sending" whose claim expired
    reclaim_cutoff = now - timedelta(minutes=RECLAIM_TIMEOUT_MINUTES)
    reclaimed = OutboundMessage.objects.filter(
        status="sending",
        claimed_at__lte=reclaim_cutoff,
    ).update(status="queued", claimed_at=None, updated_at=now)
    if reclaimed:
        logger.warning(
            "Reclaimed %d abandoned sending messages back to queued.", reclaimed
        )

    # 2. Claim in a short atomic transaction
    with transaction.atomic():
        messages = list(
            OutboundMessage.objects.select_for_update(skip_locked=True)
            .filter(status="queued", next_attempt_at__lte=now)
            .order_by("next_attempt_at")[:batch_size]
        )
        if not messages:
            return 0

        claimed_ids = [msg.pk for msg in messages]
        OutboundMessage.objects.filter(pk__in=claimed_ids).update(
            status="sending",
            claimed_at=now,
            updated_at=now,
        )

    # 3. Send outside the transaction
    processed_count = 0
    for message in messages:
        processed_count += 1
        chat_id = str(message.chat_id)

        # Check if chat has been blocked by the merchant
        if Shop.objects.filter(
            telegram_chat_id=chat_id, blocked_at__isnull=False
        ).exists():
            message.status = "cancelled"
            message.claimed_at = None
            message.last_error = "Chat is blocked by merchant (Shop.blocked_at is set)."
            message.save(
                update_fields=["status", "claimed_at", "last_error", "updated_at"]
            )
            logger.info(
                "Outbox message %s cancelled: chat %s is blocked.", message.pk, chat_id
            )
            continue

        # Never fake a successful send. Without a token the message would be
        # marked "sent" and lost; mark it dead so it surfaces in admin/health.
        mock_send = getattr(settings, "TELEGRAM_MOCK_SEND", False)
        if not mock_send and not telegram_api.is_configured():
            message.status = "dead"
            message.claimed_at = None
            message.last_error = "TELEGRAM_BOT_TOKEN is not configured."
            message.save(
                update_fields=["status", "claimed_at", "last_error", "updated_at"]
            )
            logger.error(
                "Outbox message %s dead: TELEGRAM_BOT_TOKEN is not configured.",
                message.pk,
            )
            continue

        _throttle(chat_id, rate_limit=rate_limit)

        text = message.payload.get("text", "")
        reply_markup = message.payload.get("reply_markup")

        try:
            if mock_send:
                logger.info(
                    "[Mock Telegram] Send message to chat %s: %r", chat_id, text
                )
                result = {"message_id": 12345}
            else:
                result = telegram_api.send_message(
                    chat_id=chat_id,
                    text=text,
                    reply_markup=reply_markup,
                )
            message.status = "sent"

            message.attempts += 1
            msg_id = result.get("message_id") if isinstance(result, dict) else None
            if msg_id is not None:
                message.telegram_message_id = str(msg_id)
                confirmation_request_id = message.payload.get("confirmation_request_id")
                if confirmation_request_id:
                    ConfirmationRequest.objects.filter(
                        pk=confirmation_request_id
                    ).update(
                        telegram_message_id=str(msg_id),
                        updated_at=timezone.now(),
                    )
                digest_id = message.payload.get("digest_id")
                if digest_id:
                    # Only the digest's own message id is authoritative; a
                    # reminder carries the same digest_id and must not overwrite it.
                    ConfirmationDigest.objects.filter(
                        pk=digest_id, telegram_message_id__isnull=True
                    ).update(
                        telegram_message_id=str(msg_id),
                        updated_at=timezone.now(),
                    )
            message.last_error = ""
            logger.info("Outbox message %s sent to chat %s.", message.pk, chat_id)
        except telegram_api.TelegramRetryAfter as exc:
            message.attempts += 1
            message.status = "queued"
            message.next_attempt_at = timezone.now() + timedelta(
                seconds=exc.retry_after
            )
            message.last_error = str(exc)
            logger.warning(
                "Outbox message %s RetryAfter(%ds): %s",
                message.pk,
                exc.retry_after,
                exc,
            )
        except telegram_api.TelegramForbidden as exc:
            message.status = "dead"
            message.last_error = str(exc)
            Shop.objects.filter(telegram_chat_id=chat_id).update(
                blocked_at=timezone.now()
            )
            logger.warning(
                "Outbox message %s Forbidden; marked chat %s blocked: %s",
                message.pk,
                chat_id,
                exc,
            )
        except telegram_api.TelegramBadRequest as exc:
            message.status = "dead"
            message.last_error = getattr(exc, "description", str(exc))
            logger.error(
                "Outbox message %s BadRequest; marked dead: %s", message.pk, exc
            )
        except (telegram_api.TelegramNetworkError, telegram_api.TelegramError) as exc:
            message.attempts += 1
            if message.attempts > MAX_NETWORK_ATTEMPTS:
                message.status = "dead"
                logger.error(
                    "Outbox message %s exceeded max network retries (%d); dead: %s",
                    message.pk,
                    MAX_NETWORK_ATTEMPTS,
                    exc,
                )
            else:
                backoff_secs = NETWORK_BACKOFF_SECONDS[message.attempts - 1]
                message.status = "queued"
                message.next_attempt_at = timezone.now() + timedelta(
                    seconds=backoff_secs
                )
                logger.warning(
                    "Outbox message %s network error; retry in %ds (attempt %d): %s",
                    message.pk,
                    backoff_secs,
                    message.attempts,
                    exc,
                )
            message.last_error = str(exc)
        except Exception as exc:
            # Fallback for unexpected exceptions so the loop never crashes
            message.status = "dead"
            message.last_error = f"Unexpected error: {exc}"
            logger.exception("Outbox message %s unexpected error: %s", message.pk, exc)
        finally:
            message.claimed_at = None
            message.save(
                update_fields=[
                    "status",
                    "attempts",
                    "next_attempt_at",
                    "claimed_at",
                    "last_error",
                    "telegram_message_id",
                    "updated_at",
                ]
            )

    return processed_count
