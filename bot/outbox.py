"""Transactional outbox for proactive Telegram messages.

Proactive messages (prompts, digests, reminders) are written here inside the
caller's database transaction. A separate sender process drains this table.
"""

import logging

from django.db import IntegrityError, transaction
from django.utils import timezone

from .models import OutboundMessage

logger = logging.getLogger(__name__)


def enqueue(
    chat_id: str | int,
    kind: str,
    payload: dict,
    dedupe_key: str,
    send_silently_at_night: bool = False,
) -> OutboundMessage | None:
    """Enqueue an outbound message for delivery.

    Returns the created OutboundMessage, or None if a message with this dedupe_key
    already exists (idempotent no-op).

    Wraps the create in a nested savepoint so that an IntegrityError on duplicate
    dedupe_key does not abort an outer caller's transaction.
    """
    try:
        with transaction.atomic():
            return OutboundMessage.objects.create(
                chat_id=str(chat_id),
                kind=kind,
                payload=payload,
                dedupe_key=dedupe_key,
                send_silently_at_night=send_silently_at_night,
                status="queued",
                next_attempt_at=timezone.now(),
            )
    except IntegrityError:
        logger.info(
            "Outbound message with dedupe_key %r already exists; skipping enqueue.",
            dedupe_key,
        )
        return None
