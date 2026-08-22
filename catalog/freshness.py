"""The one definition of the freshness label.

Derived at read time from `Product.last_confirmed_at` so the label can never
drift from the clock. `get_freshness` is the Python path (one product already in
hand), `freshness_case` the SQL path (annotating a queryset). Both read the same
two constants, so moving a window moves every caller together.

Deliberately *not* the same thing as `Product.confirmation_status`, which answers
a different question on a different clock (`settings.PRODUCT_STALENESS_DAYS`):
"should we prompt the merchant to reconfirm?" rather than "how much should a
buyer trust this listing right now?". The first needs stored state -- it has to
remember who has already been asked. The second is a pure function of the clock
and must never be stored.
"""

from datetime import datetime, timedelta

from django.db import models
from django.utils import timezone


class Freshness(models.TextChoices):
    """The vocabulary. Not a model field -- nothing stores these."""

    FRESH = "fresh", "Fresh"
    ALMOST_EXPIRED = "almost_expired", "Almost expired"
    EXPIRED = "expired", "Expired"


FRESH_WINDOW = timedelta(hours=48)
ALMOST_EXPIRED_WINDOW = timedelta(days=7)


def get_freshness(
    last_confirmed_at: datetime | None, now: datetime | None = None
) -> Freshness:
    """The label for a single product.

    `now` is injectable so a test can sit exactly on a boundary. Reading the
    clock inside the function makes the 48h and 7d edges unobservable: by the
    time the subtraction runs, a value set to exactly 48h ago is already past it.
    """
    if last_confirmed_at is None:
        return Freshness.EXPIRED
    age = (now or timezone.now()) - last_confirmed_at
    if age <= FRESH_WINDOW:
        return Freshness.FRESH
    if age <= ALMOST_EXPIRED_WINDOW:
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
            last_confirmed_at__gte=now - FRESH_WINDOW,
            then=models.Value(Freshness.FRESH),
        ),
        models.When(
            last_confirmed_at__gte=now - ALMOST_EXPIRED_WINDOW,
            then=models.Value(Freshness.ALMOST_EXPIRED),
        ),
        default=models.Value(Freshness.EXPIRED),
        output_field=models.CharField(),
    )
