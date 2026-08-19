from django.db import models
from django.db.models import Q

from core.models import BaseModel


class ConfirmationRequest(BaseModel):
    STATUS_CHOICES = [
        ("pending", "Pending"),
        ("confirmed", "Confirmed"),
        ("sold_out", "Sold out"),
        ("expired", "Expired"),
    ]

    product = models.ForeignKey(
        "catalog.Product",
        on_delete=models.CASCADE,
        related_name="confirmation_requests",
    )
    shop = models.ForeignKey(
        "shops.Shop",
        on_delete=models.CASCADE,
        related_name="confirmation_requests",
    )
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default="pending",
    )
    responded_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text=(
            "When the merchant answered. Stays NULL for expired requests — an "
            "expiry is the absence of an answer, not an answer."
        ),
    )
    channel = models.CharField(max_length=20, default="telegram")
    telegram_message_id = models.CharField(
        max_length=100,
        blank=True,
        null=True,
    )

    class Meta(BaseModel.Meta):
        ordering = ["-created_at"]
        constraints = [
            # The "is one already pending?" check in services.py is a read
            # followed by a write. Two sweeps racing would both pass the read and
            # send the merchant two prompts for the same product. Let the database
            # settle it.
            models.UniqueConstraint(
                fields=["product"],
                condition=Q(status="pending"),
                name="unique_pending_confirmation_per_product",
            )
        ]

    # There is no separate `sent_at`: the prompt goes out in the same transaction
    # that creates the row, so `created_at` is the send time, and `sweep_freshness`
    # keys the expiry window off it. One clock, not two.

    def __str__(self) -> str:
        return (
            f"ConfirmationRequest({self.product.name} - "
            f"{self.shop.name} [{self.status}])"
        )
