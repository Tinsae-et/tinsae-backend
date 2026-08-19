from datetime import timedelta

from django.core.exceptions import ValidationError
from django.utils import timezone

from .models import ConfirmationRequest

REQUEST_STATUS = ["pending", "confirmed", "sold_out", "expired"]

CONFIRMATION_WINDOW = timedelta(seconds=24)


def create_pending_request(product, shop, channel="telegram"):
    return ConfirmationRequest.objects.create(
        product=product, shop=shop, sent_at=timezone.now(), channel=channel
    )


def resolve_request(confirmation_request, status):
    if status.lower() not in REQUEST_STATUS:
        raise ValidationError(f"Invalid resolution: {status}")

    if status == REQUEST_STATUS[0]:
        raise ValidationError(
            f"Can not resolve a request that is already {REQUEST_STATUS[0]}"
        )

    confirmation_request.status = status
    confirmation_request.responded_at = timezone.now()
    confirmation_request.save(update_fields=["status", "responded_at"])


def expire_confirmation_requests():
    now = timezone.now()
    cutoff = now - CONFIRMATION_WINDOW
    qs = ConfirmationRequest.objects.filter(
        sent_at__lt=cutoff, status=REQUEST_STATUS[0]
    )

    return qs.update(responded_at=now, status=REQUEST_STATUS[3])
