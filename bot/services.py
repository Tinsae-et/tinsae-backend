import logging
import re

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from catalog.models import Product
from catalog.services import answer_confirmation_request, apply_confirmation
from shops.models import Shop

from . import messages, outbox
from . import telegram_api as telegram
from .keyboards import prompt_markup
from .models import (
    LINK_CODE_ALPHABET,
    LINK_CODE_LENGTH,
    LINK_CODE_PREFIX,
    ConfirmationDigest,
    ConfirmationRequest,
    ProcessedUpdate,
    ShopLinkCode,
)
from .parsing import (
    AFFIRMATIVE as AFFIRMATIVE,
)
from .parsing import (
    NEGATIVE as NEGATIVE,
)
from .parsing import (
    parse_answer,
    parse_listing_callback,
    parse_prompt_callback,
)
from .selectors import pending_request_for_chat

logger = logging.getLogger(__name__)

# /myproducts buttons are product-keyed and colon-separated, so they cannot be
# confused with the underscore-separated `confirm_<request_pk>` prompt callbacks.
PRODUCT_CALLBACK_ACTIONS = {"pconfirm": True, "pdeny": False}

# A well-formed code sitting inside a longer message. The trailing lookahead stops
# a mistyped, over-long code from matching on its first seven characters and
# quietly binding the shop the merchant did not mean.
LINK_CODE_RE = re.compile(
    rf"{re.escape(LINK_CODE_PREFIX)}[{LINK_CODE_ALPHABET}]{{{LINK_CODE_LENGTH}}}"
    r"(?![0-9A-Z])"
)

# Backwards compatibility alias for the prompt keyboard constructor.
_prompt_markup = prompt_markup


def prompt_text(product: Product) -> str:
    """The merchant-facing prompt: what we're asking about, and how to answer.

    Public and composed up front rather than inline in the send path, so
    `send_prompt` can show the wording without a token configured and the two
    openings cannot drift apart.

    The YES/NO line is a promise, not decoration: those words are in
    `AFFIRMATIVE`/`NEGATIVE` below, and `parse_answer` lowercases before matching.
    """
    return messages.prompt(
        brand=product.brand,
        name=product.name,
        price=product.price,
        currency=product.currency,
        shop_name=product.shop.name,
        is_available=product.is_available,
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

    Returns the pending ConfirmationRequest, or None if the prompt was skipped.
    Enqueues an OutboundMessage in the same transaction so delivery is handled
    asynchronously by the outbox sender with retries.

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
            outbox.enqueue(
                chat_id=shop.telegram_chat_id,
                kind="prompt",
                payload={
                    "text": text,
                    "reply_markup": _prompt_markup(confirmation_request.pk),
                    "confirmation_request_id": confirmation_request.pk,
                },
                dedupe_key=f"prompt:{confirmation_request.pk}",
            )
    except IntegrityError:
        logger.info(
            "Skipping product %s: a concurrent run already created a request.",
            product.pk,
        )
        return None

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
    parsed = parse_listing_callback(callback_data)
    if parsed is None:
        logger.warning("Unrecognised product callback: %r", callback_data)
        return None

    action, product_id, _page = parsed

    try:
        product = Product.objects.select_related("shop").get(pk=product_id)
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
) -> str:
    """Apply a merchant's inline-button response.

    `chat_id` is required and must match the shop that owns the product. Without
    it, any Telegram user could mark any product fresh or out of stock.

    Returns "applied" | "already_answered" | "not_found".
    """
    parsed = parse_prompt_callback(callback_data)
    if parsed is None:
        logger.warning("Unrecognised callback data: %r", callback_data)
        return "not_found"

    action, request_id = parsed
    available = action == "confirm"

    outcome, product = answer_confirmation_request(
        chat_id=chat_id, request_id=request_id, available=available
    )

    if outcome == "not_found":
        logger.warning(
            "Callback %r from chat %s rejected: request %s not found or foreign.",
            callback_data,
            chat_id,
            request_id,
        )
        return "not_found"

    if outcome == "already_answered":
        logger.info(
            "Ignoring callback %r: request %s is already answered.",
            callback_data,
            request_id,
        )
        if telegram.is_configured():
            if callback_query_id:
                try:
                    telegram.answer_callback(
                        callback_query_id, messages.response_recorded()
                    )
                except telegram.TelegramError as exc:
                    logger.error("answerCallbackQuery failed: %s", exc)
            if message_id:
                try:
                    telegram.edit_message(
                        chat_id, message_id, messages.already_recorded()
                    )
                except telegram.TelegramError as exc:
                    logger.error("editMessageText failed: %s", exc)
        return "already_answered"

    response_text = messages.telegram_callback_response(
        product_name=product.name,
        confirmed=available,
    )

    if telegram.is_configured():
        if callback_query_id:
            try:
                telegram.answer_callback(
                    callback_query_id, messages.response_recorded()
                )
            except telegram.TelegramError as exc:
                logger.error("answerCallbackQuery failed: %s", exc)
        if message_id:
            try:
                telegram.edit_message(chat_id, message_id, response_text)
            except telegram.TelegramError as exc:
                logger.error("editMessageText failed: %s", exc)

    logger.info("Applied callback %r to product %s.", callback_data, product.pk)
    return "applied"


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

    outcome, product = answer_confirmation_request(
        chat_id=chat_id, request_id=request.pk, available=confirmed
    )
    if outcome == "already_answered":
        return "inactive", product
    if outcome == "not_found":
        return "orphan", None

    logger.info("Applied text reply %r to product %s.", text, product.pk)
    return outcome, product


def issue_link_code(shop: Shop, *, created_by=None) -> ShopLinkCode:
    """Mint a link code for a shop, retiring any it already has outstanding.

    One live code per shop. Two in circulation means the merchant who is read the
    older one gets a baffling failure, and it doubles the window in which a
    leaked code still works. Retiring by expiry rather than deletion keeps the
    audit trail and makes the stale one report "expired", which is the truth.
    """
    now = timezone.now()
    with transaction.atomic():
        # `.update()` does not fire auto_now, so updated_at is set by hand.
        ShopLinkCode.objects.filter(
            shop=shop, used_at__isnull=True, expires_at__gt=now
        ).update(expires_at=now, updated_at=now)

        # `code` is assigned blind in save(); the unique constraint is the guard.
        for _ in range(5):
            try:
                with transaction.atomic():
                    return ShopLinkCode.objects.create(shop=shop, created_by=created_by)
            except IntegrityError:
                continue
        raise RuntimeError("Could not generate a unique Telegram link code.")


def normalise_link_code(text: str) -> str:
    """Canonicalise whatever the merchant typed, or "" if it isn't code-shaped.

    Generous on input because the code travels by voice and by copy-paste:
    lowercase, stray spaces and a dropped prefix are all forgiven. The alphabet
    and length checks are what keep it strict.
    """
    if not text:
        return ""
    compact = "".join(text.split()).upper()
    if compact.startswith(LINK_CODE_PREFIX):
        compact = compact[len(LINK_CODE_PREFIX) :]
    compact = compact.lstrip("-")
    if len(compact) != LINK_CODE_LENGTH:
        return ""
    if any(character not in LINK_CODE_ALPHABET for character in compact):
        return ""
    return f"{LINK_CODE_PREFIX}{compact}"


def resolve_link_code(text: str) -> str:
    """The code the merchant meant, or "" if there isn't one.

    Two passes, because codes arrive two ways. The whole message may *be* the
    code — then case, spacing and a dropped prefix are all forgiven, since it is
    read aloud down a phone line as often as it is pasted. Or the code sits
    inside a sentence ("here's my code TIN-ABC2345"), where it must be
    well-formed and prefixed before it is safe to pick out.
    """
    whole = normalise_link_code(text)
    if whole:
        return whole
    if not text:
        return ""
    match = LINK_CODE_RE.search(text.upper())
    return match.group(0) if match else ""


def looks_like_link_code(text: str) -> bool:
    """Is this bare message about linking, rather than an answer to a prompt?

    Routes free text in `run_bot` before it reaches `handle_text_reply`. A prefix
    is required either way — at the start of the message, or on a code embedded
    in one. `/link` is more forgiving, because there the merchant has already
    said what they mean.

    A message that merely *opens* with the prefix routes here even when the code
    is malformed: that earns "I don't recognise that code", which is the useful
    failure, rather than being handed to the yes/no parser and nagged about
    stock. And because the prefix is uppercase and punctuated, no member of
    AFFIRMATIVE/NEGATIVE can reach this path — `tests_linking` pins that against
    the real vocabularies rather than a sample.
    """
    if not text:
        return False
    if "".join(text.split()).upper().startswith(LINK_CODE_PREFIX):
        return True
    return bool(LINK_CODE_RE.search(text.upper()))


def link_shop_to_chat(code: str, chat_id: str) -> tuple[str, Shop | None]:
    """Bind a Telegram chat to the Shop named by a one-time code.

    Returns `(outcome, shop)`, where outcome is one of "linked", "relinked",
    "already_linked", "unknown", "expired", "used". The caller owns the wording —
    the same split as `handle_text_reply`.

    Linking is additive per chat and exclusive per shop: one chat may run several
    shops (see `selectors.shops_for_chat`), but a shop has exactly one chat.

    The code is never logged. It is a bearer credential, and a log aggregator is
    not a place to leave one.
    """
    normalised = resolve_link_code(code)
    if not normalised or chat_id in (None, ""):
        return "unknown", None

    chat_id = str(chat_id)
    now = timezone.now()

    with transaction.atomic():
        try:
            # Locked, not merely fetched: two chats racing one code must not
            # both come away believing they won it.
            link_code = (
                ShopLinkCode.objects.select_for_update()
                .select_related("shop")
                .get(code=normalised)
            )
        except ShopLinkCode.DoesNotExist:
            logger.info("Link attempt from chat %s with an unknown code.", chat_id)
            return "unknown", None

        shop = link_code.shop

        if link_code.used_at is not None:
            # A merchant who taps send twice should hear "you're already linked",
            # not "invalid code". Anyone else replaying it hears "used".
            if (
                str(link_code.used_by_chat_id) == chat_id
                and str(shop.telegram_chat_id) == chat_id
            ):
                return "already_linked", shop
            logger.warning(
                "Chat %s replayed a spent link code for shop %s (spent by chat %s).",
                chat_id,
                shop.pk,
                link_code.used_by_chat_id,
            )
            return "used", None

        if link_code.expires_at <= now:
            logger.info("Link attempt from chat %s with an expired code.", chat_id)
            return "expired", None

        previous = str(shop.telegram_chat_id or "")
        if previous == chat_id:
            outcome = "already_linked"
        elif previous:
            outcome = "relinked"
            logger.warning(
                "Shop %s moved from chat %s to chat %s.", shop.pk, previous, chat_id
            )
        else:
            outcome = "linked"

        shop.telegram_chat_id = chat_id
        shop.blocked_at = None
        shop.save(update_fields=["telegram_chat_id", "blocked_at", "updated_at"])

        link_code.used_at = now
        link_code.used_by_chat_id = chat_id
        link_code.save(update_fields=["used_at", "used_by_chat_id", "updated_at"])

    logger.info("Chat %s linked to shop %s (%s).", chat_id, shop.pk, outcome)
    return outcome, shop


def unlink_chat(chat_id: str | int | None) -> list[Shop]:
    """Clear telegram_chat_id and blocked_at for every shop bound to this chat.

    Returns the shops that were unlinked.
    """
    if chat_id in (None, ""):
        return []
    chat_str = str(chat_id)
    with transaction.atomic():
        shops = list(Shop.objects.filter(telegram_chat_id=chat_str))
        if not shops:
            return []
        now = timezone.now()
        Shop.objects.filter(telegram_chat_id=chat_str).update(
            telegram_chat_id="",
            blocked_at=None,
            updated_at=now,
        )
        for shop in shops:
            shop.telegram_chat_id = ""
            shop.blocked_at = None
        logger.info("Unlinked chat %s from %d shop(s).", chat_str, len(shops))
        return shops


def disconnect_shop(shop: Shop) -> None:
    """Clear a shop's Telegram binding and blocked status.

    Leaves link codes intact for audit.
    """
    shop.telegram_chat_id = ""
    shop.blocked_at = None
    shop.save(update_fields=["telegram_chat_id", "blocked_at", "updated_at"])
    logger.info("Disconnected Telegram from shop %s.", shop.pk)


def record_update(update_id: int) -> bool:
    """Record an inbound Telegram update_id to prevent duplicate handling.

    Returns True if newly recorded (fresh), or False if already seen (duplicate).
    """
    try:
        with transaction.atomic():
            ProcessedUpdate.objects.create(update_id=update_id)
        return True
    except IntegrityError:
        return False


def answer_confirmation_digest(
    chat_id: str | int | None, digest_id: int, *, available: bool = True
) -> tuple[str, int]:
    """Resolve all pending requests attached to a ConfirmationDigest.

    Outcomes:
        ("confirmed" | "sold_out", count_answered) on success
        ("already_answered", 0) if digest is already closed
        ("not_found", 0) if digest does not exist or chat_id does not own the shop
    """
    if chat_id in (None, ""):
        return "not_found", 0

    try:
        digest = ConfirmationDigest.objects.select_related("shop").get(pk=digest_id)
    except (ConfirmationDigest.DoesNotExist, ValueError):
        return "not_found", 0

    shop_chat_id = digest.shop.telegram_chat_id
    if not shop_chat_id or str(shop_chat_id) != str(chat_id):
        return "not_found", 0

    if digest.closed_at is not None:
        return "already_answered", 0

    target_outcome = "confirmed" if available else "sold_out"
    count = 0

    with transaction.atomic():
        requests = list(digest.requests.filter(status="pending"))
        for req in requests:
            outcome, _ = answer_confirmation_request(
                chat_id=chat_id,
                request_id=req.pk,
                available=available,
            )
            if outcome == target_outcome:
                count += 1

        digest.closed_at = timezone.now()
        digest.save(update_fields=["closed_at", "updated_at"])

    logger.info(
        "Chat %s answered digest %s (%s, %d items).",
        chat_id,
        digest_id,
        target_outcome,
        count,
    )
    return target_outcome, count


class ReviewItem(dict):
    """Review item dict with attribute access."""

    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError:
            raise AttributeError(name) from None


class ReviewProgress(dict):
    """Review progress dict with attribute access."""

    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError:
            raise AttributeError(name) from None


def _build_review_item(req: ConfirmationRequest, now: timezone.datetime) -> ReviewItem:
    product = req.product
    label = f"{product.brand} {product.name}".strip()
    price_str = f"{product.price:,.2f} {product.currency}"
    last_confirmed = product.last_confirmed_at or product.created_at
    age_days = max(0, (now - last_confirmed).days) if last_confirmed else 0
    return ReviewItem(
        request_id=req.pk,
        label=label,
        price_str=price_str,
        age_days=age_days,
        product_id=product.pk,
    )


def start_review(
    chat_id: str,
    digest_id: int,
) -> tuple[str, int, ReviewItem | None, ReviewProgress | None]:
    """Begin stepping through confirmation requests for a weekly digest.

    Returns (outcome, cursor, item, progress) where outcome in:
      "item" | "done" | "not_found"
    """
    if chat_id in (None, ""):
        return "not_found", 0, None, None

    try:
        digest = ConfirmationDigest.objects.select_related("shop").get(pk=digest_id)
    except (ConfirmationDigest.DoesNotExist, ValueError):
        return "not_found", 0, None, None

    shop_chat_id = digest.shop.telegram_chat_id
    if not shop_chat_id or str(shop_chat_id) != str(chat_id):
        return "not_found", 0, None, None

    requests = list(digest.requests.select_related("product").order_by("pk"))
    total = len(requests)
    now = timezone.now()

    if total == 0:
        return "done", 0, None, ReviewProgress(index=0, total=0, confirmed=0, sold=0)

    if digest.closed_at is not None:
        confirmed_count = sum(1 for r in requests if r.status == "confirmed")
        sold_count = sum(1 for r in requests if r.status == "sold_out")
        return (
            "done",
            digest.review_cursor,
            None,
            ReviewProgress(
                index=total,
                total=total,
                confirmed=confirmed_count,
                sold=sold_count,
            ),
        )

    start_index = digest.review_cursor
    target_idx = None
    for idx in range(start_index, total):
        if requests[idx].status == "pending":
            target_idx = idx
            break

    if target_idx is None:
        for idx in range(0, start_index):
            if requests[idx].status == "pending":
                target_idx = idx
                break

    if target_idx is None:
        if digest.closed_at is None:
            digest.closed_at = now
            digest.save(update_fields=["closed_at", "updated_at"])
        confirmed_count = sum(1 for r in requests if r.status == "confirmed")
        sold_count = sum(1 for r in requests if r.status == "sold_out")
        return (
            "done",
            total,
            None,
            ReviewProgress(
                index=total,
                total=total,
                confirmed=confirmed_count,
                sold=sold_count,
            ),
        )

    if digest.review_cursor != target_idx:
        digest.review_cursor = target_idx
        digest.save(update_fields=["review_cursor", "updated_at"])

    req = requests[target_idx]
    item = _build_review_item(req, now)
    confirmed_count = sum(1 for r in requests if r.status == "confirmed")
    sold_count = sum(1 for r in requests if r.status == "sold_out")
    progress = ReviewProgress(
        index=target_idx + 1,
        total=total,
        confirmed=confirmed_count,
        sold=sold_count,
    )
    return "item", target_idx, item, progress


def review_next(
    chat_id: str,
    digest_id: int | None = None,
    action: str = "y",
    request_id: int = 0,
) -> tuple[str, int, ReviewItem | None, ReviewProgress | None]:
    """Process an action on a review item and advance to the next pending item.

    Action in:
      'y' (available), 'n' (sold out), 's' (skip), 'x' (stop / pause).

    Returns (outcome, cursor, item, progress) where outcome in:
      "item" | "done" | "paused" | "not_found"
    """
    if chat_id in (None, ""):
        return "not_found", 0, None, None

    digest = None
    if digest_id is not None:
        try:
            digest = ConfirmationDigest.objects.select_related("shop").get(pk=digest_id)
        except (ConfirmationDigest.DoesNotExist, ValueError):
            return "not_found", 0, None, None
    else:
        try:
            cr = ConfirmationRequest.objects.select_related("digest__shop").get(
                pk=request_id
            )
            digest = cr.digest
        except (ConfirmationRequest.DoesNotExist, ValueError):
            return "not_found", 0, None, None

    if (
        digest is None
        or not digest.shop.telegram_chat_id
        or str(digest.shop.telegram_chat_id) != str(chat_id)
    ):
        return "not_found", 0, None, None

    requests = list(digest.requests.select_related("product").order_by("pk"))
    total = len(requests)
    now = timezone.now()

    if action == "x":
        confirmed_count = sum(1 for r in requests if r.status == "confirmed")
        sold_count = sum(1 for r in requests if r.status == "sold_out")
        return (
            "paused",
            digest.review_cursor,
            None,
            ReviewProgress(
                index=digest.review_cursor + 1,
                total=total,
                confirmed=confirmed_count,
                sold=sold_count,
            ),
        )

    req_pos = None
    for idx, r in enumerate(requests):
        if r.pk == request_id:
            req_pos = idx
            break

    if action in ("y", "n"):
        available = action == "y"
        answer_confirmation_request(
            chat_id=chat_id, request_id=request_id, available=available
        )
        if req_pos is not None:
            requests[req_pos].status = "confirmed" if available else "sold_out"

    if req_pos is not None:
        next_index = req_pos + 1
    else:
        next_index = digest.review_cursor + 1

    target_idx = None
    for idx in range(next_index, total):
        if requests[idx].status == "pending":
            target_idx = idx
            break

    if target_idx is None:
        for idx in range(0, next_index):
            if requests[idx].status == "pending":
                target_idx = idx
                break

    if target_idx is None:
        digest.review_cursor = total
        digest.closed_at = now
        digest.save(update_fields=["review_cursor", "closed_at", "updated_at"])
        confirmed_count = sum(1 for r in requests if r.status == "confirmed")
        sold_count = sum(1 for r in requests if r.status == "sold_out")
        return (
            "done",
            total,
            None,
            ReviewProgress(
                index=total,
                total=total,
                confirmed=confirmed_count,
                sold=sold_count,
            ),
        )

    digest.review_cursor = target_idx
    digest.save(update_fields=["review_cursor", "updated_at"])

    req = requests[target_idx]
    item = _build_review_item(req, now)
    confirmed_count = sum(1 for r in requests if r.status == "confirmed")
    sold_count = sum(1 for r in requests if r.status == "sold_out")
    progress = ReviewProgress(
        index=target_idx + 1,
        total=total,
        confirmed=confirmed_count,
        sold=sold_count,
    )
    return "item", target_idx, item, progress
