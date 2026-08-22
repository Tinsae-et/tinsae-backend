"""Write-side domain logic for the catalogue.

Product state transitions live here rather than in `bot/services.py` because the
bot is one delivery channel, not the domain. A web view that needs to record a
merchant's answer imports from here; nothing in this module knows what Telegram
is. Prompt formatting, message sending, and callback parsing stay in `bot/`.

`ConfirmationRequest` is imported from `bot.models` — it arguably belongs in this
app too, but moving a model costs a migration. The import is acyclic:
`bot/models.py` pulls in only `core.models` and Django, and declares its foreign
keys as strings.
"""

import logging

from django.db import transaction
from django.utils import timezone

from bot.models import ConfirmationRequest

from .models import ConfirmationStatus, Product

logger = logging.getLogger(__name__)


def resolve_request(request: ConfirmationRequest, status: str) -> None:
    """Close out a pending request. The single writer of `responded_at`.

    `expired` is accepted but records no `responded_at`: a timeout is the absence
    of an answer, not an answer. `sweep_freshness` expires in bulk and never comes
    through here, but the single-request path has to agree with it.
    """
    if status not in ("confirmed", "sold_out", "expired"):
        raise ValueError(f"Not a resolution: {status!r}")

    if request.status != "pending":
        logger.info(
            "Ignoring resolve of request %s: already %s.", request.pk, request.status
        )
        return

    request.status = status
    if status != "expired":
        request.responded_at = timezone.now()
    request.save(update_fields=["status", "responded_at", "updated_at"])


def apply_confirmation(
    product: Product,
    *,
    confirmed: bool,
    request: ConfirmationRequest | None = None,
) -> None:
    """Record a merchant's answer about one product.

    The single writer of the confirmation clock, shared by the inline-prompt
    callback and the /myproducts listing. Both answers restart the clock: "out of
    stock" is fresh information about an unavailable product — availability and
    freshness are separate axes.

    `request` is optional because a product listed by /myproducts usually has no
    outstanding ConfirmationRequest; with prompts disabled, none was ever sent.
    """
    with transaction.atomic():
        if request is not None:
            resolve_request(request, "confirmed" if confirmed else "sold_out")

        product.last_confirmed_at = timezone.now()
        product.confirmation_status = ConfirmationStatus.FRESH
        product.is_available = confirmed
        product.save(
            update_fields=[
                "last_confirmed_at",
                "confirmation_status",
                "is_available",
                "updated_at",
            ]
        )
