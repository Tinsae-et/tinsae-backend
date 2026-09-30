"""The one definition of the freshness label.

Derived at read time from `Product.last_confirmed_at` on the unified confirmation
clock (CONFIRMATION_INTERVAL_DAYS and CONFIRMATION_HIDDEN_AFTER_DAYS) so the label
can never drift from the clock. `get_freshness` is the Python path (one product
already in hand), `freshness_case` the SQL path (annotating a queryset). Both read
the same two windows from settings:

    FRESH:           age <= CONFIRMATION_INTERVAL_DAYS (7d)
    ALMOST_EXPIRED:  7d < age <= CONFIRMATION_HIDDEN_AFTER_DAYS (14d)
    EXPIRED:         age > 14d

`Product.confirmation_status` and the merchant-prompting loop share this same 7-day
due window. The derived freshness tiers are pure functions of the clock and must
never be stored.
"""

from datetime import datetime, timedelta

from django.conf import settings
from django.db import models
from django.utils import timezone


class Freshness(models.TextChoices):
    """The vocabulary. Not a model field -- nothing stores these."""

    FRESH = "fresh", "Fresh"
    ALMOST_EXPIRED = "almost_expired", "Almost expired"
    EXPIRED = "expired", "Expired"


def fresh_window() -> timedelta:
    """The window in which a confirmation is considered fresh (default 7 days)."""
    return timedelta(days=getattr(settings, "CONFIRMATION_INTERVAL_DAYS", 7))


def almost_expired_window() -> timedelta:
    """The threshold after which an unconfirmed listing is expired/hidden (default 14 days)."""
    return timedelta(days=getattr(settings, "CONFIRMATION_HIDDEN_AFTER_DAYS", 14))


def __getattr__(name: str):
    if name == "FRESH_WINDOW":
        return fresh_window()
    if name == "ALMOST_EXPIRED_WINDOW":
        return almost_expired_window()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__():
    return list(globals().keys()) + ["FRESH_WINDOW", "ALMOST_EXPIRED_WINDOW"]


def get_freshness(
    last_confirmed_at: datetime | None, now: datetime | None = None
) -> Freshness:
    """The label for a single product.

    `now` is injectable so a test can sit exactly on a boundary. Reading the
    clock inside the function makes the 7d and 14d edges unobservable: by the
    time the subtraction runs, a value set to exactly 7d ago is already past it.
    """
    if last_confirmed_at is None:
        return Freshness.EXPIRED
    age = (now or timezone.now()) - last_confirmed_at
    if age <= fresh_window():
        return Freshness.FRESH
    if age <= almost_expired_window():
        return Freshness.ALMOST_EXPIRED
    return Freshness.EXPIRED


def freshness_case(now: datetime | None = None) -> models.Case:
    """The same rule, expressed for the database.

    `last_confirmed_at >= now - window` is the exact complement of
    `age <= window`, boundary included, so this and `get_freshness` agree on the
    instant and not merely on the band. A NULL fails every `__gte` and falls
    through to the default, which is the null -> expired rule.
    """
    now = now or timezone.now()
    return models.Case(
        models.When(
            last_confirmed_at__gte=now - fresh_window(),
            then=models.Value(Freshness.FRESH),
        ),
        models.When(
            last_confirmed_at__gte=now - almost_expired_window(),
            then=models.Value(Freshness.ALMOST_EXPIRED),
        ),
        default=models.Value(Freshness.EXPIRED),
        output_field=models.CharField(),
    )
