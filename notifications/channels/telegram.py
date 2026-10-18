"""Telegram delivery channel for notifications."""

import logging

from django.conf import settings

from bot.outbox import enqueue
from notifications.kinds import Kind
from notifications.models import Notification
from shops.models import Shop

logger = logging.getLogger(__name__)


def deliver(notification: Notification, spec: Kind) -> None:
    """Enqueue a proactive Telegram message for a notification.

    Finds the shop owned by the notification recipient. If the shop is missing,
    has no telegram_chat_id, or is blocked, logs with PKs only and returns.
    """
    shop_id = notification.payload.get("shop_id")
    if not shop_id:
        logger.info(
            "Cannot deliver telegram notification %s: shop_id missing from payload",
            notification.pk,
        )
        return

    shop = Shop.objects.filter(pk=shop_id, owner=notification.recipient).first()
    if not shop:
        logger.info(
            "Cannot deliver telegram notification %s: shop %s not found or not owned"
            " by user %s",
            notification.pk,
            shop_id,
            notification.recipient_id,
        )
        return

    if not shop.telegram_chat_id:
        logger.info(
            "Cannot deliver telegram notification %s: shop %s has no telegram_chat_id",
            notification.pk,
            shop.pk,
        )
        return

    if shop.blocked_at is not None:
        logger.info(
            "Cannot deliver telegram notification %s: shop %s is blocked",
            notification.pk,
            shop.pk,
        )
        return

    url = settings.FRONTEND_URL.rstrip("/") + notification.link
    text = spec.telegram_text(notification.payload, url) if spec.telegram_text else ""
    enqueue(
        chat_id=shop.telegram_chat_id,
        kind=f"notify:{notification.kind}",
        payload={"text": text, "notification_id": notification.pk},
        dedupe_key=f"notification:{notification.pk}:telegram",
        send_silently_at_night=spec.telegram_silent_at_night,
    )
