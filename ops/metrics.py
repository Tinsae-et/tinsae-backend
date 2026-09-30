"""Every number on the ops health page, computed in one place.

`ops` sits below `bot` in the dependency graph: it reads `shops`, `catalog` and
`bot`, and nothing reads it. It deliberately does not live in `core`, which is a
leaf utility and must never import upward into the domain apps.
"""

from datetime import timedelta

from django.conf import settings
from django.db.models import Count, Q
from django.utils import timezone

from bot import telegram_api as telegram
from bot.models import ConfirmationRequest
from catalog.models import ConfirmationStatus, Product
from shops.models import Shop

# A merchant who answered either way responded. "Out of stock" is an answer --
# it restarts the confirmation clock exactly like "still available" does.
#
# These must be values `ConfirmationRequest.STATUS_CHOICES` actually allows. They
# were not: this read ("confirmed", "denied") long after migration 0004 renamed
# "denied" to "sold_out", so every sold-out answer counted as silence and the
# merchants who gave it were queued for unverification. A filter on a status that
# cannot occur fails silently -- it just counts zero -- so the guard is a test,
# `tests.test_review_regressions.OpsMetricsReadTheStatusTheSystemActuallyWrites`.
ANSWERED_STATUSES = ("confirmed", "sold_out")

# How many requests a shop must have let lapse before we call it unresponsive.
# One expired prompt is not evidence, it is a merchant who was busy that week,
# and the remedy this list feeds is unverification.
NEVER_RESPONDS_MIN_RESOLVED = 2


def _pct(part: int, whole: int) -> float | None:
    """None, never 0.0, when there is nothing to divide.

    The difference carries the whole meaning: 0% is "we asked and they never
    answered", None is "we never asked". Collapsing them would flag every shop
    as a non-responder on a system where ENABLE_TELEGRAM_BOT_PROMPTS has always
    been False -- which is every shop, today.
    """
    if not whole:
        return None
    return round(100 * part / whole, 1)


def _catalog_health(staleness_cutoff) -> dict:
    counts = Product.objects.filter(is_available=True).aggregate(
        available=Count("pk"),
        fresh=Count("pk", filter=Q(confirmation_status=ConfirmationStatus.FRESH)),
        stale=Count("pk", filter=Q(confirmation_status=ConfirmationStatus.STALE)),
        unconfirmed=Count(
            "pk", filter=Q(confirmation_status=ConfirmationStatus.UNCONFIRMED)
        ),
        never_confirmed=Count("pk", filter=Q(last_confirmed_at__isnull=True)),
    )

    # The stored column is what buyers actually receive as a status, so it is
    # the number to report. Recomputing the same predicate against the live clock
    # says what the column *would* be after a sweep. The gap between them is the
    # one metric that catches "nobody ever scheduled the cron" -- without it, a
    # sweep that stopped running looks identical to a perfectly healthy catalog.
    stale_by_clock = Product.objects.needing_confirmation(staleness_cutoff).count()

    counts["unavailable"] = Product.objects.filter(is_available=False).count()
    counts["fresh_pct"] = _pct(counts["fresh"], counts["available"])
    counts["stale_by_clock"] = stale_by_clock

    # Both stored values mean "past the clock", so drift measures against their
    # sum. Comparing against `stale` alone would count every product the sweep
    # correctly marked UNCONFIRMED as drift, and a loop working exactly as
    # designed would report itself broken -- worse than no metric, because this
    # is the number that decides whether anyone goes looking.
    past_clock = counts["stale"] + counts["unconfirmed"]
    counts["past_clock"] = past_clock
    counts["sweep_drift"] = stale_by_clock - past_clock
    counts["sweep_current"] = stale_by_clock == past_clock
    return counts


def _request_health(expiry_cutoff) -> dict:
    counts = ConfirmationRequest.objects.aggregate(
        total=Count("pk"),
        pending=Count("pk", filter=Q(status="pending")),
        # Pending past the expiry window: the sweep should have cleared these.
        # Every one of them is blocking a re-prompt for its product, so a
        # non-zero count here means the loop is jammed, not merely quiet.
        pending_overdue=Count(
            "pk", filter=Q(status="pending", created_at__lt=expiry_cutoff)
        ),
        confirmed=Count("pk", filter=Q(status="confirmed")),
        sold_out=Count("pk", filter=Q(status="sold_out")),
        expired=Count("pk", filter=Q(status="expired")),
    )
    answered = counts["confirmed"] + counts["sold_out"]
    counts["answered"] = answered
    counts["resolved"] = answered + counts["expired"]
    counts["response_rate"] = _pct(answered, counts["resolved"])
    return counts


def _shop_rows() -> list[dict]:
    """One row per shop. `distinct=True` is load-bearing.

    Two multi-valued joins (products and confirmation_requests) multiply against
    each other; without it every count is inflated by the size of the other
    relation.
    """
    shops = Shop.objects.annotate(
        available_products=Count(
            "products", distinct=True, filter=Q(products__is_available=True)
        ),
        # Both stored values mean "past the clock", so the per-shop column counts
        # them together for the same reason `sweep_drift` does -- filtering on
        # STALE alone would quietly drop every product whose merchant went
        # silent, which is precisely the shop this row exists to surface.
        stale_products=Count(
            "products",
            distinct=True,
            filter=Q(
                products__is_available=True,
                products__confirmation_status__in=(
                    ConfirmationStatus.STALE,
                    ConfirmationStatus.UNCONFIRMED,
                ),
            ),
        ),
        answered=Count(
            "confirmation_requests",
            distinct=True,
            filter=Q(confirmation_requests__status__in=ANSWERED_STATUSES),
        ),
        expired=Count(
            "confirmation_requests",
            distinct=True,
            filter=Q(confirmation_requests__status="expired"),
        ),
        pending=Count(
            "confirmation_requests",
            distinct=True,
            filter=Q(confirmation_requests__status="pending"),
        ),
    ).order_by("name")

    rows = []
    for shop in shops:
        # Pending requests stay out of the denominator: a prompt sent an hour ago
        # is not yet a failure to respond. The sweep moves it to `expired` once
        # the window closes, and it counts against the shop then.
        resolved = shop.answered + shop.expired
        rows.append(
            {
                "id": shop.pk,
                "name": shop.name,
                "slug": shop.slug,
                "city": shop.city,
                "is_verified": shop.is_verified,
                "has_chat_id": bool(shop.telegram_chat_id),
                "available_products": shop.available_products,
                "stale_products": shop.stale_products,
                "answered": shop.answered,
                "expired": shop.expired,
                "pending": shop.pending,
                "resolved": resolved,
                "response_rate": _pct(shop.answered, resolved),
            }
        )
    return rows


def _attention(rows: list[dict]) -> dict:
    """Three separate lists, because they need three different remedies.

    Lumping them together is how a shop we have never once contacted ends up
    proposed for unverification.
    """
    return {
        # Asked repeatedly, answered nothing: the real follow-up candidates.
        "never_responds": [
            r
            for r in rows
            if r["answered"] == 0 and r["resolved"] >= NEVER_RESPONDS_MIN_RESOLVED
        ],
        # We cannot reach them at all. Fix the chat ID; do not unverify.
        "unreachable": [r for r in rows if not r["has_chat_id"]],
        # Reachable but never prompted. Expected while prompts are disabled.
        "never_asked": [
            r
            for r in rows
            if r["has_chat_id"] and not r["resolved"] and not r["pending"]
        ],
    }


def collect_health() -> dict:
    """The whole page, from one call.

    The JSON endpoint and the admin page both render this dict, so the two
    surfaces cannot report different figures for the same instant.
    """
    now = timezone.now()
    staleness_cutoff = now - timedelta(days=settings.CONFIRMATION_INTERVAL_DAYS)
    expiry_cutoff = now - timedelta(hours=settings.CONFIRMATION_REQUEST_EXPIRY_HOURS)
    rows = _shop_rows()

    return {
        "generated_at": now,
        # Without this block a page of zeroes is unreadable: you cannot tell
        # "the loop is broken" from "outbound prompts were never switched on".
        "config": {
            "staleness_days": settings.CONFIRMATION_INTERVAL_DAYS,
            "expiry_hours": settings.CONFIRMATION_REQUEST_EXPIRY_HOURS,
            "prompts_enabled": getattr(settings, "ENABLE_TELEGRAM_BOT_PROMPTS", False),
            "telegram_configured": telegram.is_configured(),
            "never_responds_min_resolved": NEVER_RESPONDS_MIN_RESOLVED,
        },
        "catalog": _catalog_health(staleness_cutoff),
        "requests": _request_health(expiry_cutoff),
        "shops": rows,
        "attention": _attention(rows),
    }
