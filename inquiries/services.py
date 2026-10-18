"""Write paths for buyer-shop conversations.

The web API and the Telegram bot both call these; neither carries business
rules of its own. Every function that touches an existing inquiry takes the
acting user and the inquiry's ID, and resolves it through the participant-scoped
selectors -- so a foreign ID fails exactly like a missing one, and the caller's
role (buyer or shop) is derived from the row, never trusted from the caller.
"""

from functools import partial

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from catalog.models import ProductStatus
from core.notify import notify

from .models import MAX_BODY_LENGTH, Inquiry, InquiryMessage
from .selectors import inquiries_for_participant, role_of

NEW_MESSAGE = "inquiry_message"


def _validate_message(body, sent_via):
    if not isinstance(body, str) or not body.strip():
        raise ValidationError({"body": "Message body cannot be empty."})
    body = body.strip()
    if len(body) > MAX_BODY_LENGTH:
        raise ValidationError(
            {"body": f"Message body cannot exceed {MAX_BODY_LENGTH} characters."}
        )
    if sent_via not in InquiryMessage.SentVia.values:
        raise ValidationError({"sent_via": "Invalid message channel."})
    return body


def _lock_for(user, inquiry_id):
    """Fetch and row-lock an inquiry `user` takes part in.

    Raises Inquiry.DoesNotExist for a foreign ID as well as a missing one.
    """
    return (
        inquiries_for_participant(user)
        .select_related("shop")
        .select_for_update(of=("self",))
        .get(pk=inquiry_id)
    )


def _same_conversation(buyer, shop, product):
    queryset = Inquiry.objects.filter(buyer=buyer, shop=shop)
    if product is None:
        return queryset.filter(product__isnull=True)
    return queryset.filter(product=product)


def _open_or_reopen(buyer, shop, product):
    """Return the one open conversation for this key, reopening or creating it.

    Two concurrent first messages can both miss the open row; the partial
    unique constraint lets exactly one insert win, and the loser picks up the
    winner's row instead of erroring.
    """
    conversation = _same_conversation(buyer, shop, product)

    inquiry = (
        conversation.filter(status=Inquiry.Status.OPEN).select_for_update().first()
    )
    if inquiry is not None:
        return inquiry

    try:
        with transaction.atomic():
            inquiry = (
                conversation.filter(status=Inquiry.Status.CLOSED)
                .order_by("-last_message_at")
                .select_for_update()
                .first()
            )
            if inquiry is not None:
                inquiry.status = Inquiry.Status.OPEN
                inquiry.save(update_fields=["status", "updated_at"])
                return inquiry
            return Inquiry.objects.create(buyer=buyer, shop=shop, product=product)
    except IntegrityError:
        return conversation.filter(status=Inquiry.Status.OPEN).select_for_update().get()


def _append_message(inquiry, sender_role, body, sent_via):
    """Add a message to a locked inquiry and tell the other side.

    Writing to a closed conversation reopens it, unless a newer conversation
    for the same buyer, shop and product is already open.
    """
    if not inquiry.is_open:
        conflict = (
            _same_conversation(inquiry.buyer_id, inquiry.shop_id, inquiry.product_id)
            .filter(status=Inquiry.Status.OPEN)
            .exclude(pk=inquiry.pk)
            .exists()
        )
        if conflict:
            raise ValidationError(
                "A newer conversation with this shop is already open."
            )
        inquiry.status = Inquiry.Status.OPEN

    inquiry.last_message_at = timezone.now()
    if sender_role == InquiryMessage.SenderRole.BUYER:
        inquiry.shop_unread += 1
        recipient = inquiry.shop
    else:
        inquiry.buyer_unread += 1
        recipient = inquiry.buyer
    inquiry.save(
        update_fields=[
            "status",
            "last_message_at",
            "buyer_unread",
            "shop_unread",
            "updated_at",
        ]
    )
    message = InquiryMessage.objects.create(
        inquiry=inquiry, sender_role=sender_role, body=body, sent_via=sent_via
    )
    # After commit, so a rolled-back message never produces a notification.
    transaction.on_commit(
        partial(notify, recipient, NEW_MESSAGE, inquiry=inquiry, message=message)
    )
    return message


@transaction.atomic
def start_inquiry(buyer, shop, body, sent_via, product=None):
    """A buyer writes to a shop, optionally about one of its products.

    Continues the open conversation for (buyer, shop, product) if there is one,
    reopens the last closed one otherwise, and only creates a new conversation
    when neither exists.
    """
    body = _validate_message(body, sent_via)
    if shop.owner_id == buyer.pk:
        raise ValidationError("You cannot send an inquiry to your own shop.")
    if product is not None:
        if product.shop_id != shop.pk:
            raise ValidationError({"product": "This product is not sold by this shop."})
        if product.status == ProductStatus.DRAFT:
            raise ValidationError({"product": "This product is not available."})

    inquiry = _open_or_reopen(buyer, shop, product)
    _append_message(inquiry, InquiryMessage.SenderRole.BUYER, body, sent_via)
    return inquiry


@transaction.atomic
def send_message(user, inquiry_id, body, sent_via):
    """`user` replies in a conversation they take part in, as buyer or shop."""
    body = _validate_message(body, sent_via)
    inquiry = _lock_for(user, inquiry_id)
    return _append_message(inquiry, role_of(user, inquiry), body, sent_via)


@transaction.atomic
def mark_read(user, inquiry_id):
    """Clear `user`'s side of the unread counter."""
    inquiry = _lock_for(user, inquiry_id)
    if role_of(user, inquiry) == InquiryMessage.SenderRole.BUYER:
        inquiry.buyer_unread = 0
        inquiry.save(update_fields=["buyer_unread", "updated_at"])
    else:
        inquiry.shop_unread = 0
        inquiry.save(update_fields=["shop_unread", "updated_at"])
    return inquiry


@transaction.atomic
def close_inquiry(user, inquiry_id):
    """Either participant closes the conversation; writing again reopens it."""
    inquiry = _lock_for(user, inquiry_id)
    if inquiry.is_open:
        inquiry.status = Inquiry.Status.CLOSED
        inquiry.save(update_fields=["status", "updated_at"])
    return inquiry
