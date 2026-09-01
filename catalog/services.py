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
from django.db.models import (
    Case,
    ExpressionWrapper,
    F,
    FloatField,
    Q,
    Value,
    When,
)
from django.utils import timezone

from bot.models import ConfirmationRequest

from .models import ConfirmationStatus, Product, SearchLog

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

    `request` is optional because the caller does not always have one to hand --
    /myproducts and the dashboard both answer about a product, not about a
    prompt. When it is omitted the product's outstanding prompt, if any, is
    looked up and resolved anyway: the answer is the same answer regardless of
    which channel carried it.
    """
    with transaction.atomic():
        if request is None:
            # A merchant who answers in the dashboard has answered. Whichever
            # channel they used, an outstanding prompt for this product *is*
            # being responded to, and letting it lapse instead records a
            # non-response against a shop that did reply -- `ops.metrics` puts
            # `expired` in the denominator of the response rate, and two of them
            # lands the shop in `never_responds`, whose remedy is unverification.
            #
            # `select_for_update` because the Telegram callback may be resolving
            # the same row concurrently. `resolve_request` no-ops on anything
            # already resolved, so the loser of that race does nothing.
            request = (
                ConfirmationRequest.objects.select_for_update()
                .filter(product=product, status="pending")
                .first()
            )

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
    """Put a paused listing back on the storefront. The mirror of `pause_listing`.

    Deliberately *not* `apply_confirmation(confirmed=True)`, which is what this
    used to be -- and what the paragraph above says must never happen. Pausing
    makes no stock claim, so un-pausing retracts none either: together they are
    a round trip that leaves the clock exactly where it started. Routing resume
    through the confirmation service made the pair a two-click freshness reset
    on a listing of any age, which is the laundering `pause_listing` refuses to
    allow one function earlier.

    A merchant asserting the item really is available again does that through
    `confirm`. That act costs one tap either side of a pause, and it is the same
    act -- which is the property that makes the confirmation clock mean anything.
    """
    product.is_available = True
    product.save(update_fields=["is_available", "updated_at"])


# Provisional, and deliberately small. Every entry here is a guess about what
# buyers type, made before there was any evidence -- which is the thing TIN-58
# warned against. `SearchLog` now records the misses, so the honest way to
# grow this list is to read the frequent `result_count=0` queries and add what
# they show, not to brainstorm more pairs. It moves to a table once it needs
# editing more often than the app deploys.
SYNONYMS = {
    "phone": ["mobile", "smartphone"],
    "mobile": ["phone", "smartphone"],
    "laptop": ["computer", "notebook"],
    "computer": ["laptop", "notebook"],
}

SEARCH_FIELDS = ("name", "brand", "category__name", "description")

# Ranking weights, baked into the tier values rather than applied as multipliers
# so the whole formula reads in one place. Untuned: they are ordered correctly
# relative to each other, but the magnitudes are a first guess pending logs.
FRESH_WEIGHT = 0.30
STALE_WEIGHT = 0.10
VERIFIED_WEIGHT = 0.15


def expand_term(word: str) -> set[str]:
    """One typed word plus its synonyms."""
    return {word, *SYNONYMS.get(word, ())}


def build_search_filter(query: str) -> Q:
    """AND across the words typed, OR within one word's synonyms and fields.

    The direction matters and the previous version had it backwards: it ORed
    every term together, so each extra word a buyer typed *widened* the result
    set. "samsung laptop" returned every Samsung phone and every laptop of any
    brand, and the more specific the buyer was the worse the results got.

    ANDing the words makes each one a constraint, which is what a buyer means by
    typing it. Synonyms still OR, because "phone" and "mobile" are one
    constraint expressed two ways, not two constraints.
    """
    combined = Q()
    for word in query.lower().split():
        per_word = Q()
        for variant in expand_term(word):
            for field in SEARCH_FIELDS:
                per_word |= Q(**{f"{field}__icontains": variant})
        combined &= per_word
    return combined


def apply_search(queryset, query: str):
    """Filter `queryset` to products matching `query`, best match first.

    Takes a queryset rather than building its own so the caller's filters --
    price, category, availability, freshness -- compose with search instead of
    being lost. It orders the result; a caller that wants an explicit sort
    (`?ordering=price`) just calls `.order_by()` again, which replaces this one.
    """
    return (
        queryset.filter(build_search_filter(query))
        .annotate(
            # Relevance runs 0..2 and dominates on purpose: a buyer who types
            # "laptop" wants laptops. Freshness and verification break ties
            # between comparably relevant products; they do not override
            # relevance. The ordering this replaced sorted on `last_confirmed_at`
            # first, which made relevance unreachable in practice -- two products
            # almost never share a confirmation timestamp, so the sort ended
            # before it ever compared similarity.
            typo_score=(
                TrigramSimilarity("name", query) + TrigramSimilarity("brand", query)
            ),
            # The three tiers mirror `ProductViewSet.freshness_rank` and must
            # keep agreeing with it: a product confirmed 40 days ago still
            # carries more signal than one never confirmed at all, so
            # never-confirmed scores 0 and sits below stale.
            freshness_score=Case(
                When(last_confirmed_at__isnull=True, then=Value(0.0)),
                When(
                    confirmation_status=ConfirmationStatus.FRESH,
                    then=Value(FRESH_WEIGHT),
                ),
                default=Value(STALE_WEIGHT),
                output_field=FloatField(),
            ),
            verified_boost=Case(
                When(shop__is_verified=True, then=Value(VERIFIED_WEIGHT)),
                default=Value(0.0),
                output_field=FloatField(),
            ),
        )
        .annotate(
            rank_score=ExpressionWrapper(
                F("typo_score") + F("freshness_score") + F("verified_boost"),
                output_field=FloatField(),
            )
        )
        # `nulls_last` because Postgres puts NULLs first on DESC, which is what
        # floated never-confirmed listings to the top of every result page. `pk`
        # last so equal-scoring rows keep a stable order across pages -- without
        # it, pagination can repeat or skip rows between requests.
        .order_by(
            "-rank_score",
            F("last_confirmed_at").desc(nulls_last=True),
            "-pk",
        )
    )


def log_search(raw_query: str, result_count: int, user=None) -> None:
    """Record one buyer search. Never raises.

    Wrapped because this runs on the response path of the busiest read endpoint
    in the app: a full disk or a lock on `catalog_searchlog` must degrade
    logging, not turn a working search into a 500. The traffic it records is
    worth having; it is not worth an outage.
    """
    normalized = " ".join(raw_query.lower().split())
    try:
        SearchLog.objects.create(
            raw_query=raw_query[:200],
            normalized=normalized[:200],
            result_count=result_count or 0,
            user=user if (user is not None and user.is_authenticated) else None,
        )
    except Exception:
        logger.exception("Could not log search query %r.", raw_query)
