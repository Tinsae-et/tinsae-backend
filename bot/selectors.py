"""Read-side helpers for the inbound bot.

Kept separate from `services.py` so the write paths there stay short enough to
audit. Nothing here mutates.
"""

from datetime import timedelta

from django.conf import settings
from django.db.models import F
from django.utils import timezone

from catalog.models import Product, ProductStatus
from shops.models import Shop

from .models import ConfirmationDigest, ConfirmationRequest, OutboundMessage

# Limit the number of restockable (out-of-stock) products loaded into /myproducts.
# Due-for-confirmation products always precede restockable products, so restockable
# items never crowd out due items on page 1. Capping restockable products (default 24,
# or 3 pages of 8 items) prevents historical sold listings from creating unbounded pages.
MYPRODUCTS_RESTOCKABLE_LIMIT = 24


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


def due_products_for_shop(shop: Shop, cutoff) -> list[Product]:
    """Products belonging to a shop that need confirmation, excluding pending requests.

    One query using Product.objects.needing_confirmation(cutoff) filtered by shop
    and .exclude(confirmation_requests__status="pending").
    """
    return list(
        Product.objects.needing_confirmation(cutoff)
        .filter(shop=shop)
        .exclude(confirmation_requests__status="pending")
        .select_related("shop")
        .order_by(F("last_confirmed_at").asc(nulls_first=True), "pk")
    )


def listing_for_chat(chat_id) -> tuple[list[Shop], list[Product]]:
    """Everything /myproducts needs, derived only from the inbound chat.

    Returns (shops, products), where products contains, in stable order:
      1. due-for-confirmation: is_available=True, status=LIVE, needing_confirmation
         (never-confirmed first, then oldest, pk tiebreak).
      2. restockable: is_available=False, status in (LIVE, SOLD) (most recently confirmed/
         updated first, capped at MYPRODUCTS_RESTOCKABLE_LIMIT).

    DRAFT products are excluded from both sections. Due items always lead so they are
    never crowded out on page 1.
    """
    shops = shops_for_chat(chat_id)
    if not shops:
        return [], []

    cutoff = timezone.now() - timedelta(days=settings.CONFIRMATION_INTERVAL_DAYS)
    due_products = list(
        Product.objects.needing_confirmation(cutoff)
        .filter(shop__in=shops, status=ProductStatus.LIVE)
        .select_related("shop")
        # Never-confirmed first, then oldest: lead with what we know least about.
        # pk breaks ties so paging cannot drop or repeat a row between pages.
        .order_by(F("last_confirmed_at").asc(nulls_first=True), "pk")
    )

    limit = getattr(settings, "MYPRODUCTS_RESTOCKABLE_LIMIT", MYPRODUCTS_RESTOCKABLE_LIMIT)
    restockable_products = list(
        Product.objects.filter(
            shop__in=shops,
            is_available=False,
            status__in=[ProductStatus.LIVE, ProductStatus.SOLD],
        )
        .select_related("shop")
        .order_by(F("last_confirmed_at").desc(nulls_last=True), "-updated_at", "pk")[
            :limit
        ]
    )

    return shops, due_products + restockable_products


def pending_request_for_chat(chat_id, reply_to_message_id=None):
    """Find the ConfirmationRequest a free-text answer belongs to.

    Returns `(request, reason)`. On a match, `reason` is None; otherwise the
    request is None and `reason` says why, so the caller can explain itself.

    The chat is the only authorisation there is: nothing is ever taken from the
    message body, so a merchant can only answer their own shops' prompts.
    """
    if chat_id in (None, ""):
        return None, "orphan"

    requests = ConfirmationRequest.objects.select_related("product", "shop").filter(
        shop__telegram_chat_id=str(chat_id)
    )

    if reply_to_message_id is not None:
        # An explicit reply names the prompt exactly. Look it up regardless of
        # status, so answering a prompt that has since expired is reported as
        # such instead of quietly landing on whatever else is pending.
        replied = requests.filter(telegram_message_id=str(reply_to_message_id)).first()
        if replied is not None:
            if replied.status != "pending":
                return None, "inactive"
            return replied, None
        # Otherwise they replied to some other message in the chat; fall through
        # to the single-pending rule.

    pending = list(requests.filter(status="pending")[:2])
    if not pending:
        return None, "orphan"
    if len(pending) > 1:
        return None, "ambiguous"
    return pending[0], None


def resolve_digest_for_chat(
    chat_id, reply_to_message_id=None
) -> ConfirmationDigest | None:
    """Find the ConfirmationDigest a text reply belongs to.

    - If reply_to_message_id is given, match a ConfirmationDigest whose
      telegram_message_id == str(reply_to_message_id) and whose shop is linked
      to the chat.
    - Else select open digest(s) (closed_at__isnull=True) for shops linked to
      this chat:
      - Exactly one open digest across the chat's shops -> return it.
      - Zero, or more than one -> return None.
    """
    if chat_id in (None, ""):
        return None

    shops = shops_for_chat(chat_id)
    if not shops:
        return None

    if reply_to_message_id is not None:
        # The digest's own message id is the primary match.
        digest = (
            ConfirmationDigest.objects.filter(
                shop__in=shops,
                telegram_message_id=str(reply_to_message_id),
            )
            .select_related("shop")
            .first()
        )
        if digest is not None:
            return digest

        # A reminder (or any resend) is a separate OutboundMessage carrying the
        # same digest_id; resolve it too so replying to the latest message works.
        sent = (
            OutboundMessage.objects.filter(
                telegram_message_id=str(reply_to_message_id),
                payload__has_key="digest_id",
            )
            .only("payload")
            .first()
        )
        if sent is not None:
            digest_id = sent.payload.get("digest_id")
            if digest_id:
                return (
                    ConfirmationDigest.objects.filter(pk=digest_id, shop__in=shops)
                    .select_related("shop")
                    .first()
                )
        return None

    open_digests = list(
        ConfirmationDigest.objects.filter(
            shop__in=shops,
            closed_at__isnull=True,
        ).select_related("shop")[:2]
    )
    if len(open_digests) == 1:
        return open_digests[0]
    return None
