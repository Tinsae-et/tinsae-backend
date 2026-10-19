"""Participant-scoped reads for notifications."""

from .kinds import in_app_kinds
from .models import Notification


def notifications_for(user):
    """Return the notification queryset for `user`, newest first.

    Only in-app kinds (see `in_app_kinds`), so obsolete or channel-only kinds
    never break rendering. List ordering is [-created_at, -id] for stable
    ordering when timestamps tie.
    """
    return Notification.objects.filter(
        recipient=user,
        kind__in=in_app_kinds(),
    ).order_by("-created_at", "-id")
