"""Celery tasks for inquiries."""

import logging

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(name="inquiries.tasks.remind_unanswered_inquiries")
def remind_unanswered_inquiries() -> int:
    """Nudge shops about conversations where the buyer is still waiting."""
    from .services import (
        remind_unanswered_inquiries as run_remind_unanswered_inquiries,
    )

    return run_remind_unanswered_inquiries()
