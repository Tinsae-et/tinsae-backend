from .models import ConfirmationRequest
from django.utils import timezone

REQUEST_STATUS = [
    "pending",
    "confirmed",
    "sold_out",
    "expired"
]

def create_pending_request(product, shop, channel):
    return ConfirmationRequest.objects.create(
        product = product,
        shop = shop,
        sent_at = timezone.now(),
        channel = channel
    )

def resolve_request(confirmation_request, status):
    if status.lower() not in REQUEST_STATUS:
        raise ValidationError(f"Invalid resolution: {status}")
    
    if status == "pending":
        raise ValidationError("Can not resolve a request that is already pending")
    
    confirmation_request.status = status
    responded_at = timezone.now()
    confirmation_request.save(update_fields=["status", "responded_at"])