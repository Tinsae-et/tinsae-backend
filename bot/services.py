import logging
import re
from decimal import Decimal

from django.conf import settings
from django.db import IntegrityError, transaction

from catalog.models import Product
from catalog.services import apply_confirmation

from . import telegram_api as telegram
from .models import ConfirmationRequest
from .selectors import pending_request_for_chat

logger = logging.getLogger(__name__)

# /myproducts buttons are product-keyed and colon-separated, so they cannot be
# confused with the underscore-separated `confirm_<request_pk>` prompt callbacks.
PRODUCT_CALLBACK_ACTIONS = {"pconfirm": True, "pdeny": False}

# Free-text answers, matched as whole normalised phrases. Kept deliberately
# small and exact: a wrong guess writes to the catalogue, and substring matching
# would read "no longer available" as a confirmation.
_PUNCTUATION = re.compile(r"[^\w\s]", flags=re.UNICODE)

AFFIRMATIVE = {
    "yes",
    "y",
    "yeah",
    "yep",
    "yup",
    "ok",
    "okay",
    "sure",
    "available",
    "still available",
    "in stock",
    "still in stock",
    "have",
    "still have",
    "we have it",
    "አለ",
}
NEGATIVE = {
    "no",
    "n",
    "nope",
    "sold",
    "sold out",
    "soldout",
    "not available",
    "out of stock",
    "finished",
    "gone",
    "የለም",
}


def _prompt_markup(request_id: int) -> dict:
    return {
        "inline_keyboard": [
            [
                {
                    "text": "✅ Still available",
                    "callback_data": f"confirm_{request_id}",
                },
                {
                    "text": "❌ Sold out",
                    "callback_data": f"deny_{request_id}",
                },
            ]
        ]
    }


def prompt_text(product: Product) -> str:
    """The merchant-facing prompt: what we're asking about, and how to answer.

    Public and composed up front rather than inline in the send path, so
    `send_prompt` can show the wording without a token configured and the two
    openings cannot drift apart.

    The YES/NO line is a promise, not decoration: those words are in
    `AFFIRMATIVE`/`NEGATIVE` below, and `parse_answer` lowercases before matching.
    """
    shop = product.shop
    if product.is_available:
        opening = "Is this still available?"
    else:
        # We only re-ask about an unavailable product because the merchant told
        # us it was out of stock a while ago. Asking "is it still available?"
        # would read as though we had ignored them.
        opening = "Is this back in stock?"

    # A DecimalField only holds a Decimal once it has been round-tripped through
    # the database; on an instance built in memory it is still whatever was
    # assigned, and a str has no ",.2f" format. Coerce so this works either way.
    price = Decimal(product.price)

    return (
        f"{opening}\n\n"
        f"{product.brand} {product.name}\n"
        f"{price:,.2f} {product.currency} · {shop.name}\n\n"
        "Tap a button below, or just reply YES or NO."
    )


def can_prompt(product: Product, *, expiry_cutoff=None, force: bool = False) -> bool:
    """Would a prompt for this product actually be sent?

    The single source of truth for the gates, so `sweep_freshness --dry-run`
    cannot drift from the real send path. `expiry_cutoff` lets a dry run discount
    the pending requests that the real run's expiry pass would have cleared
    before it got here.

    `force` lifts the policy flag and nothing else. An unlinked shop and an
    outstanding request still block the send, whoever is asking -- one is a
    message with nowhere to go, the other is the double-send guard.
    """
    if not force and not getattr(settings, "ENABLE_TELEGRAM_BOT_PROMPTS", False):
        logger.info(
            "Telegram prompts are disabled (ENABLE_TELEGRAM_BOT_PROMPTS=False)."
        )
        return False

    shop = product.shop
    if not shop.telegram_chat_id:
        logger.info("Skipping product %s: shop %s has no chat ID.", product.pk, shop.pk)
        return False

    pending = ConfirmationRequest.objects.filter(product=product, status="pending")
    if expiry_cutoff is not None:
        pending = pending.filter(created_at__gte=expiry_cutoff)
    if pending.exists():
        logger.info("Skipping product %s: pending request exists.", product.pk)
        return False

    return True


def send_confirmation_prompt(
    product: Product, *, force: bool = False
) -> ConfirmationRequest | None:
    """Ask a shop to confirm a product is still in stock.

    Returns the pending ConfirmationRequest, or None if the prompt was skipped or
    the send failed. A failed send leaves no pending row behind, so the next sweep
    retries instead of being blocked for the whole expiry window.

    `force` is for the hand-run `send_prompt` command and lifts only the policy
    flag; see `can_prompt`.
    """
    if not can_prompt(product, force=force):
        return None

    if force and not getattr(settings, "ENABLE_TELEGRAM_BOT_PROMPTS", False):
        logger.warning(
            "Forced prompt for product %s while prompts are disabled.", product.pk
        )

    shop = product.shop
    # Composed before the transport is checked, so a tokenless run can still show
    # the merchant-facing wording.
    text = prompt_text(product)

    # can_prompt's duplicate test is a read-then-write, so a concurrent sweep can slip
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
            "[Mock Telegram] Prompt for product %s to chat %s: %r",
            product.pk,
            shop.telegram_chat_id,
            text,
        )
        return confirmation_request

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


def handle_product_callback(callback_data: str, chat_id: str) -> Product | None:
    """Apply a /myproducts button press.

    Keyed on the product rather than a ConfirmationRequest, because the listing
    shows products that were never prompted. Returns the updated product, or None
    if the callback was malformed, unknown, came from a chat that does not own the
    shop, or tried to confirm a product already marked out of stock.

    Deliberately *not* gated on ENABLE_TELEGRAM_BOT_PROMPTS: that flag is outbound
    policy ("should we ping merchants?"). A merchant who opened this listing
    themselves is already talking to us.
    """
    action, _, rest = callback_data.partition(":")
    raw_id = rest.partition(":")[0]
    if action not in PRODUCT_CALLBACK_ACTIONS or not raw_id.isdigit():
        logger.warning("Unrecognised product callback: %r", callback_data)
        return None

    try:
        product = Product.objects.select_related("shop").get(pk=int(raw_id))
    except Product.DoesNotExist:
        logger.error("No product for callback %r", callback_data)
        return None

    shop_chat_id = product.shop.telegram_chat_id
    if not shop_chat_id or str(chat_id) != str(shop_chat_id):
        logger.warning(
            "Callback %r from chat %s does not own shop %s — rejected.",
            callback_data,
            chat_id,
            product.shop_id,
        )
        return None

    # The listing only ever offers ✅ for available products, so reaching here
    # means the merchant tapped a button in an older message. Honouring it would
    # silently republish a product that is marked out of stock -- an undocumented
    # back door into the re-stock path we deliberately have not built yet.
    if PRODUCT_CALLBACK_ACTIONS[action] and not product.is_available:
        logger.info(
            "Ignoring %r: product %s is marked out of stock.",
            callback_data,
            product.pk,
        )
        return None

    # An outstanding prompt for this product has just been answered by other
    # means. Resolve it too, or the sweep treats it as still pending and the
    # merchant gets asked again once it expires.
    pending = ConfirmationRequest.objects.filter(
        product=product, status="pending"
    ).first()

    apply_confirmation(
        product, confirmed=PRODUCT_CALLBACK_ACTIONS[action], request=pending
    )
    logger.info("Applied product callback %r to product %s.", callback_data, product.pk)
    return product


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
    apply_confirmation(product, confirmed=confirmed, request=confirmation_request)

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


def parse_answer(text: str) -> bool | None:
    """Read a free-text answer as yes/no, or None if it isn't one."""
    if not text:
        return None
    normalised = " ".join(_PUNCTUATION.sub(" ", text.strip().lower()).split())
    if normalised in AFFIRMATIVE:
        return True
    if normalised in NEGATIVE:
        return False
    return None


def handle_text_reply(
    text: str,
    chat_id: str,
    reply_to_message_id=None,
) -> tuple[str, Product | None]:
    """Apply a merchant's free-text answer to their outstanding prompt.

    Returns `(outcome, product)`, where outcome is one of "confirmed",
    "sold_out", "unparsed", "ambiguous", "inactive", "orphan". The caller owns
    the reply text -- the same split as the callback path, where PTB does the
    talking.

    Matching runs before parsing on purpose: an unreadable message in a chat
    with nothing pending should hear "nothing is waiting on you", not be asked
    to answer a question nobody put to them.

    Deliberately *not* gated on ENABLE_TELEGRAM_BOT_PROMPTS: that flag is
    outbound policy. A merchant who typed at us is already talking to us.
    """
    request, reason = pending_request_for_chat(chat_id, reply_to_message_id)
    if request is None:
        return reason, None

    confirmed = parse_answer(text)
    if confirmed is None:
        return "unparsed", None

    product = request.product
    apply_confirmation(product, confirmed=confirmed, request=request)
    logger.info("Applied text reply %r to product %s.", text, product.pk)
    return ("confirmed" if confirmed else "sold_out"), product
