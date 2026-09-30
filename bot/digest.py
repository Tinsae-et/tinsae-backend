"""Weekly confirmation digest orchestration and building.

Pure builder in build_digest; Celery job entry point in send_due_digests.
"""

import logging
from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone
from telegram import InlineKeyboardMarkup

from catalog.models import Product
from shops.models import Shop

from . import keyboards, messages, outbox
from .models import ConfirmationDigest, ConfirmationRequest
from .selectors import due_products_for_shop

logger = logging.getLogger(__name__)


def build_digest(
    shop: Shop,
    digest_id: int = 0,
    cutoff: timezone.datetime | None = None,
    due_products: list[Product] | None = None,
) -> tuple[list[Product], str, InlineKeyboardMarkup]:
    """Build the list of due products, digest message text, and inline keyboard markup.

    Text and markup come from bot/messages.py and bot/keyboards.py.
    Does no ORM beyond due_products_for_shop.
    """
    if cutoff is None:
        cutoff = timezone.now() - timedelta(days=settings.CONFIRMATION_INTERVAL_DAYS)

    if due_products is None:
        due_products = due_products_for_shop(shop, cutoff)

    items = []
    for idx, product in enumerate(due_products[:15], start=1):
        label = f"{product.brand} {product.name}".strip()
        price_str = f"{product.price:,.2f} {product.currency}"
        items.append((idx, label, price_str))

    more_count = max(0, len(due_products) - 15)
    text = messages.digest(shop.name, items, more_count)
    markup = keyboards.digest_markup(digest_id)
    return due_products, text, markup


def send_due_digests() -> int:
    """Send weekly confirmation digests for shops whose interval is due.

    Iterates over linked, unblocked shops; evaluates cadence gate and due items;
    creates ConfirmationDigest and pending ConfirmationRequests; enqueues an
    OutboundMessage in the same transaction.
    """
    now = timezone.now()
    today = timezone.localtime(now).date()
    cutoff = now - timedelta(days=settings.CONFIRMATION_INTERVAL_DAYS)

    digests_sent = 0
    candidate_shops = Shop.objects.exclude(telegram_chat_id="").filter(
        blocked_at__isnull=True
    )

    for shop in candidate_shops:
        # Cadence gate: skip if a digest exists within the interval
        last = shop.confirmation_digests.order_by("-for_date").first()
        if last and (today - last.for_date).days < settings.CONFIRMATION_INTERVAL_DAYS:
            continue

        due = due_products_for_shop(shop, cutoff)
        if not due:
            continue

        with transaction.atomic():
            digest, created = ConfirmationDigest.objects.get_or_create(
                shop=shop, for_date=today
            )
            if not created:
                continue

            for product in due:
                try:
                    with transaction.atomic():
                        base_time = (
                            product.last_confirmed_at
                            or product.created_at
                            or now
                        )
                        expires_at = base_time + timedelta(
                            days=settings.CONFIRMATION_HIDDEN_AFTER_DAYS
                        )
                        ConfirmationRequest.objects.create(
                            product=product,
                            shop=shop,
                            digest=digest,
                            status="pending",
                            expires_at=expires_at,
                        )
                except IntegrityError:
                    continue

            _, text, markup = build_digest(
                shop,
                digest_id=digest.pk,
                cutoff=cutoff,
                due_products=due,
            )
            payload_markup = markup.to_dict() if hasattr(markup, "to_dict") else markup
            outbox.enqueue(
                chat_id=shop.telegram_chat_id,
                kind="digest",
                payload={
                    "text": text,
                    "reply_markup": payload_markup,
                    "digest_id": digest.pk,
                },
                dedupe_key=f"digest:{shop.pk}:{today}",
            )
            digests_sent += 1
            logger.info(
                "Enqueued digest for shop %s (%d products, for_date=%s).",
                shop.pk,
                len(due),
                today,
            )

    return digests_sent
