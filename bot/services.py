import logging

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from catalog.models import ConfirmationStatus, Product

from . import telegram
from .models import ConfirmationRequest

logger = logging.getLogger(__name__)


def _prompt_markup(request_id: int) -> dict:
    return {
        "inline_keyboard": [
            [
                {
                    "text": "✅ Yes, in stock",
                    "callback_data": f"confirm_{request_id}",
                },
                {
                    "text": "❌ No, out of stock",
                    "callback_data": f"deny_{request_id}",
                },
            ]
        ]
    }


def send_confirmation_prompt(product: Product) -> ConfirmationRequest | None:
    """Ask a shop to confirm a product is still in stock.

    Returns the pending ConfirmationRequest, or None if the prompt was skipped or
    the send failed. A failed send leaves no pending row behind, so the next sweep
    retries instead of being blocked for the whole expiry window.
    """
    if not getattr(settings, "ENABLE_TELEGRAM_BOT_PROMPTS", False):
        logger.info(
            "Telegram prompts are disabled (ENABLE_TELEGRAM_BOT_PROMPTS=False)."
        )
        return None

    shop = product.shop
    if not shop.telegram_chat_id:
        logger.info("Skipping product %s: shop %s has no chat ID.", product.pk, shop.pk)
        return None

    if ConfirmationRequest.objects.filter(product=product, status="pending").exists():
        logger.info("Skipping product %s: pending request exists.", product.pk)
        return None

    # The check above is a read-then-write, so a concurrent sweep can slip
    # between the two. The partial unique constraint on ConfirmationRequest is
    # the real guard; this catches losing that race.
    try:
        with transaction.atomic():
            confirmation_request = ConfirmationRequest.objects.create(
                product=product, shop=shop, status="pending"
            )
    except IntegrityError:
        logger.info(
            "Skipping product %s: a concurrent run already created a request.",
            product.pk,
        )
        return None

    if not telegram.is_configured():
        logger.info(
            "[Mock Telegram] Prompt for product %s to chat %s",
            product.pk,
            shop.telegram_chat_id,
        )
        return confirmation_request

    if product.is_available:
        text = f"Is '{product.name}' still available at {shop.name}?"
    else:
        # We only re-ask about an unavailable product because the merchant told
        # us it was out of stock a while ago. Asking "is it still available?"
        # would read as though we had ignored them.
        text = f"Is '{product.name}' back in stock at {shop.name}?"

    try:
        result = telegram.send_message(
            chat_id=shop.telegram_chat_id,
            text=text,
            reply_markup=_prompt_markup(confirmation_request.pk),
        )
    except telegram.TelegramError as exc:
        # Never leave a phantom pending row — it would suppress retries until the
        # request expires, and the sweep would report a prompt that never landed.
        confirmation_request.delete()
        logger.error("Prompt for product %s failed: %s", product.pk, exc)
        return None

    message_id = result.get("message_id")
    if message_id is not None:
        confirmation_request.telegram_message_id = str(message_id)
        confirmation_request.save(update_fields=["telegram_message_id", "updated_at"])
    return confirmation_request


def handle_telegram_callback(
    callback_data: str,
    chat_id: str,
    callback_query_id: str | None = None,
    message_id: str | None = None,
) -> bool:
    """Apply a merchant's inline-button response.

    `chat_id` is required and must match the shop that owns the product. Without
    it, any Telegram user could mark any product fresh or out of stock.
    """
    action, _, raw_id = callback_data.partition("_")
    if action not in ("confirm", "deny") or not raw_id.isdigit():
        logger.warning("Unrecognised callback data: %r", callback_data)
        return False

    try:
        confirmation_request = ConfirmationRequest.objects.select_related(
            "product", "shop"
        ).get(pk=int(raw_id))
    except ConfirmationRequest.DoesNotExist:
        logger.error("No ConfirmationRequest for callback %r", callback_data)
        return False

    if str(chat_id) != str(confirmation_request.shop.telegram_chat_id):
        logger.warning(
            "Callback %r from chat %s does not own shop %s — rejected.",
            callback_data,
            chat_id,
            confirmation_request.shop_id,
        )
        return False

    if confirmation_request.status != "pending":
        logger.info(
            "Ignoring callback %r: request is already %s.",
            callback_data,
            confirmation_request.status,
        )
        return False

    product = confirmation_request.product

    confirmed = action == "confirm"

    with transaction.atomic():
        # Both answers are signals from the merchant, so both restart the
        # confirmation clock. "Out of stock" is fresh information about an
        # unavailable product — availability and freshness are separate axes.
        confirmation_request.status = "confirmed" if confirmed else "denied"
        product.last_confirmed_at = timezone.now()
        product.confirmation_status = ConfirmationStatus.FRESH
        product.is_available = confirmed

        confirmation_request.save(update_fields=["status", "updated_at"])
        product.save(
            update_fields=[
                "last_confirmed_at",
                "confirmation_status",
                "is_available",
                "updated_at",
            ]
        )

    response_text = (
        f"✅ Thank you! '{product.name}' is marked in stock."
        if confirmed
        else f"❌ Got it — '{product.name}' is marked out of stock."
    )

    if telegram.is_configured():
        if callback_query_id:
            try:
                telegram.answer_callback(callback_query_id, "Response recorded")
            except telegram.TelegramError as exc:
                logger.error("answerCallbackQuery failed: %s", exc)
        if message_id:
            try:
                telegram.edit_message(chat_id, message_id, response_text)
            except telegram.TelegramError as exc:
                logger.error("editMessageText failed: %s", exc)

    logger.info("Applied callback %r to product %s.", callback_data, product.pk)
    return True
