"""In-app delivery channel for notifications."""

from notifications.kinds import Kind
from notifications.models import Notification


def deliver(notification: Notification, spec: Kind) -> None:
    """Deliver an in-app notification.

    No-op: the Notification row itself is the in-app notification.
    """
    pass
