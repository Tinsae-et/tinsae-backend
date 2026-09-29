"""Celery tasks for the Telegram bot and confirmation loop."""

import logging

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(name="bot.tasks.smoke_heartbeat")
def smoke_heartbeat() -> str:
    """Trivial periodic smoke task verifying Celery and Beat connectivity."""
    logger.info("Celery smoke heartbeat task executed successfully.")
    return "ok"


@shared_task(name="bot.tasks.drain_outbox")
def drain_outbox() -> int:
    """Drain due queued outbound messages from the outbox."""
    from .sender import drain_outbox as run_drain_outbox

    return run_drain_outbox()


@shared_task(name="bot.tasks.send_due_digests")
def send_due_digests() -> int:
    """Send weekly confirmation digests to linked merchants."""
    from .digest import send_due_digests as run_send_due_digests

    return run_send_due_digests()
