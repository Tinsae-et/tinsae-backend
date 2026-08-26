"""Write-side domain logic for the catalogue.

Product state transitions live here rather than in `bot/services.py` because the
bot is one delivery channel, not the domain. A web view that needs to record a
merchant's answer imports from here; nothing in this module knows what Telegram
is. Prompt formatting, message sending, and callback parsing stay in `bot/`.

`ConfirmationRequest` is imported from `bot.models` — it arguably belongs in this
app too, but moving a model costs a migration. The import is acyclic:
`bot/models.py` pulls in only `core.models` and Django, and declares its foreign
keys as strings.
"""

import logging

from django.contrib.postgres.search import TrigramSimilarity
from django.db import transaction
from django.db.models import Case, IntegerField, Q, Value, When
from django.utils import timezone

from bot.models import ConfirmationRequest

from .models import ConfirmationStatus, Product

logger = logging.getLogger(__name__)


def resolve_request(request: ConfirmationRequest, status: str) -> None:
    """Close out a pending request. The single writer of `responded_at`.

    `expired` is accepted but records no `responded_at`: a timeout is the absence
    of an answer, not an answer. `sweep_freshness` expires in bulk and never comes
    through here, but the single-request path has to agree with it.
    """
    if status not in ("confirmed", "sold_out", "expired"):
        raise ValueError(f"Not a resolution: {status!r}")

    if request.status != "pending":
        logger.info(
            "Ignoring resolve of request %s: already %s.", request.pk, request.status
        )
        return

    request.status = status
    if status != "expired":
        request.responded_at = timezone.now()
    request.save(update_fields=["status", "responded_at", "updated_at"])


def apply_confirmation(
    product: Product,
    *,
    confirmed: bool,
    request: ConfirmationRequest | None = None,
) -> None:
    """Record a merchant's answer about one product.

    The single writer of the confirmation clock, shared by the inline-prompt
    callback and the /myproducts listing. Both answers restart the clock: "out of
    stock" is fresh information about an unavailable product — availability and
    freshness are separate axes.

    `request` is optional because a product listed by /myproducts usually has no
    outstanding ConfirmationRequest; with prompts disabled, none was ever sent.
    """
    with transaction.atomic():
        if request is not None:
            resolve_request(request, "confirmed" if confirmed else "sold_out")

        product.last_confirmed_at = timezone.now()
        product.confirmation_status = ConfirmationStatus.FRESH
        product.is_available = confirmed
        product.save(
            update_fields=[
                "last_confirmed_at",
                "confirmation_status",
                "is_available",
                "updated_at",
            ]
        )


def pause_listing(product: Product) -> None:
    """Take a listing off the storefront without making a stock claim.

    Deliberately *not* `apply_confirmation(confirmed=False)`. "Sold out" is an
    answer -- the merchant is telling us something true about stock right now, so
    it restarts the clock. Pausing is a refusal to answer: the listing is hidden
    because the merchant does not want it shown, which says nothing about whether
    the item exists. Restarting the clock here would let a merchant launder a
    stale listing fresh by pausing and resuming it.

    So the clock is left exactly where it was, and a paused listing keeps ageing.
    """
    product.is_available = False
    product.save(update_fields=["is_available", "updated_at"])


def resume_listing(product: Product) -> None:
    """Put a paused listing back on the storefront.

    This one *is* an answer -- the merchant is asserting the item is available
    again -- so it goes through the confirmation service and restarts the clock,
    exactly as the bot's "yes, still available" callback does.
    """
    apply_confirmation(product, confirmed=True)


SYNONYMS = {
    "phone": ["mobile", "smartphone"],
    "mobile": ["phone", "smartphone"],
    "laptop": ["computer", "notebook"],
    "computer": ["laptop", "notebook"],
}


def searched_items(query: str):
    query = query.lower().strip()
    terms = {query}
    words = query.split()
    for word in words:
        terms.add(word)
        terms.update(SYNONYMS.get(word, []))
    return terms


def SearchProduct(query: str):
    terms = searched_items(query)
    search_filter = Q()
    for term in terms:
        search_filter = search_filter | (
            Q(name__icontains=term)
            | Q(brand__icontains=term)
            | Q(category__name__icontains=term)
        )
    return (
        Product.objects.filter(search_filter, is_available=True)
        .annotate(
            typo_score=(
                TrigramSimilarity("name", query) + TrigramSimilarity("brand", query)
            ),
            verified_score=Case(
                When(shop__is_verified=True, then=Value(10)),
                default=Value(0),
                output_field=IntegerField(),
            ),
        )
        .order_by("-last_confirmed_at", "-verified_score", "typo_score")
    )
