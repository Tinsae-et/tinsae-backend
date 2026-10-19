"""Participant-scoped reads for notifications."""

from .kinds import KINDS
from .models import Notification


def notifications_for(user):
    """Return the notification queryset for `user`, newest first.

    Excludes rows with unknown kinds so obsolete kinds do not break rendering.
    List ordering is [-created_at, -id] for stable ordering when timestamps tie.
    """
    return Notification.objects.filter(
        recipient=user,
        kind__in=KINDS.keys(),
    ).order_by("-created_at", "-id")
