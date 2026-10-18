"""Email delivery channel for notifications."""

from notifications.kinds import Kind
from notifications.models import Notification


def deliver(notification: Notification, spec: Kind) -> None:
    """Deliver an email notification.

    No-op at notify() time: the row stays with email_sent_at=NULL and the
    hourly send_email_digests Celery task batches and sends it.
    """
    pass
