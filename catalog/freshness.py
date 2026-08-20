from datetime import timedelta

from django.utils import timezone

FRESH_WINDOW = timedelta(hours=48)
ALMOST_EXPIRED_WINDOW = timedelta(days=7)


def get_freshness(last_confirmed_at):
    if last_confirmed_at is None:
        return "expired"
    age = timezone.now() - last_confirmed_at
    if age <= FRESH_WINDOW:
        return "fresh"
    if age <= ALMOST_EXPIRED_WINDOW:
        return "almost_expired"
    return "expired"
