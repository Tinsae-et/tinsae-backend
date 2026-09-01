"""Collision-safe slug assignment, shared by `Shop` and `Product`.

Both derive a unique slug from a name at save time, and both did it the obvious
way: scan for a free slug, then insert. That is check-then-act. Two concurrent
creates of the same name both find the same slug unused, both insert, and the
loser hits the unique index -- an unhandled IntegrityError, so an ordinary
create returns 500. The window is exactly the round trip between the SELECT and
the INSERT.

Only the database can actually arbitrate uniqueness, so the scan stays as the
way to be right in the ordinary case, and the constraint violation is caught
and retried rather than pre-empted.
"""

from django.db import IntegrityError, transaction

SLUG_ATTEMPTS = 5


def available_slug(model, base, pk=None):
    """The first unused slug in the series `base`, `base-2`, `base-3`, ...

    `pk` is excluded so re-saving an existing row does not collide with itself.
    """
    slug = base
    counter = 1
    while model._default_manager.filter(slug=slug).exclude(pk=pk).exists():
        counter += 1
        slug = f"{base}-{counter}"
    return slug


def save_with_unique_slug(instance, base, save, *args, **kwargs):
    """Assign `instance.slug` from `base` and call `save`, retrying collisions.

    `save` is the caller's own `super().save`, passed in rather than reached for
    here: that keeps this a plain function instead of something that has to sit
    in both models' MRO.

    Each attempt is its own savepoint. Without `atomic()` the failed INSERT
    poisons the surrounding transaction -- every Django TestCase and any
    ATOMIC_REQUESTS view runs inside one -- and the retry's SELECT would raise
    TransactionManagementError instead of retrying.

    An IntegrityError that is not a slug collision (a foreign key, some other
    constraint) is re-raised on the spot. Retrying it would only reproduce the
    same error five times and bury the real one.
    """
    model = type(instance)
    for attempt in range(1, SLUG_ATTEMPTS + 1):
        instance.slug = available_slug(model, base, instance.pk)
        try:
            with transaction.atomic():
                return save(*args, **kwargs)
        except IntegrityError:
            taken = (
                model._default_manager.filter(slug=instance.slug)
                .exclude(pk=instance.pk)
                .exists()
            )
            if not taken or attempt == SLUG_ATTEMPTS:
                raise
