"""Read-side helpers for the inbound bot.

Kept separate from `services.py` so the write paths there stay short enough to
audit. Nothing here mutates.
"""

from datetime import timedelta

from django.conf import settings
from django.db.models import F
from django.utils import timezone

from catalog.models import Product
from shops.models import Shop


def shops_for_chat(chat_id) -> list[Shop]:
    """Every shop that has claimed this Telegram chat.

    `Shop.telegram_chat_id` is not unique and `Shop.owner` is a plain FK, so one
    merchant can run several shops from a single chat. Returning all of them is
    the honest answer — refusing on ambiguity would lock those merchants out of
    the feature entirely.
    """
    if chat_id in (None, ""):
        return []
    return list(Shop.objects.filter(telegram_chat_id=str(chat_id)).order_by("name"))


def listing_for_chat(chat_id) -> tuple[list[Shop], list[Product]]:
    """Everything /myproducts needs, derived only from the inbound chat.

    The shop is never taken from callback data. If it were, paging would become a
    way to read another merchant's catalogue by editing a button payload.
    """
    shops = shops_for_chat(chat_id)
    if not shops:
        return [], []

    cutoff = timezone.now() - timedelta(days=settings.PRODUCT_STALENESS_DAYS)
    products = list(
        Product.objects.needing_confirmation(cutoff)
        .filter(shop__in=shops)
        .select_related("shop")
        # Never-confirmed first, then oldest: lead with what we know least about.
        # pk breaks ties so paging cannot drop or repeat a row between pages.
        .order_by(F("last_confirmed_at").asc(nulls_first=True), "pk")
    )
    return shops, products
