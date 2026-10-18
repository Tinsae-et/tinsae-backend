"""Celery tasks for notifications."""

import logging

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(name="notifications.tasks.send_email_digests")
def send_email_digests() -> int:
    """Send batched email digests to users with pending notifications."""
    from .services import send_email_digests as run_send_email_digests

    return run_send_email_digests()


@shared_task(name="notifications.tasks.purge_old_notifications")
def purge_old_notifications() -> int:
    """Purge notifications older than NOTIFICATION_RETENTION_DAYS."""
    from .services import purge_old_notifications as run_purge_old_notifications

    return run_purge_old_notifications()
