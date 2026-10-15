from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from .models import Inquiry, InquiryMessage


@transaction.atomic
def start_inquiry(
    buyer,
    shop,
    product=None,
    body=None,
    sender_role=InquiryMessage.SenderRole.BUYER,
    sent_via=InquiryMessage.SentVia.TELEGRAM,
):
    inquiry = Inquiry.open_or_create(buyer, shop, product)
    if body is not None:
        send_message(
            inquiry,
            sender_role,
            body,
            sent_via,
        )
    return inquiry


@transaction.atomic
def send_message(inquiry, sender_role, body, sent_via):
    if not isinstance(body, str) or not body.strip():
        raise ValidationError({"body": "Message body cannot be empty."})
    if len(body) > 1000:
        raise ValidationError({"body": "Message body cannot exceed 1000 characters."})
    if sender_role not in InquiryMessage.SenderRole.values:
        raise ValidationError({"sender_role": "Invalid sender role."})
    if sent_via not in InquiryMessage.SentVia.values:
        raise ValidationError({"sent_via": "Invalid message channel."})

    inquiry = Inquiry.objects.select_for_update().get(pk=inquiry.pk)
    message = InquiryMessage(
        inquiry=inquiry,
        sender_role=sender_role,
        body=body,
        sent_via=sent_via,
    )
    message.full_clean()

    now = timezone.now()
    inquiry.status = Inquiry.Status.OPEN
    inquiry.last_message_at = now
    if sender_role == InquiryMessage.SenderRole.BUYER:
        inquiry.shop_unread += 1
    else:
        inquiry.buyer_unread += 1
    inquiry.save(
        update_fields=[
            "status",
            "last_message_at",
            "buyer_unread",
            "shop_unread",
            "updated_at",
        ]
    )
    message.save()
    """
    =========================================
    THE notify() function will be called here
    =========================================
    """
    return message


@transaction.atomic
def mark_read(inquiry, role):
    if role not in InquiryMessage.SenderRole.values:
        raise ValidationError({"role": "Invalid inquiry participant role."})

    inquiry = Inquiry.objects.select_for_update().get(pk=inquiry.pk)
    if role == InquiryMessage.SenderRole.BUYER:
        inquiry.buyer_unread = 0
        inquiry.save(update_fields=["buyer_unread", "updated_at"])
    else:
        inquiry.shop_unread = 0
        inquiry.save(update_fields=["shop_unread", "updated_at"])
    return inquiry


@transaction.atomic
def close_inquiry(inquiry):
    inquiry = Inquiry.objects.select_for_update().get(pk=inquiry.pk)
    inquiry.close()
    return inquiry
