from django.db import models
from django.db.models import Q

from core.models import BaseModel


class ConfirmationRequest(BaseModel):
    STATUS_CHOICES = [
        ("pending", "Pending"),
        ("confirmed", "Confirmed"),
        ("expired", "Expired"),
        ("denied", "Denied"),
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

    def __str__(self) -> str:
        return (
            f"ConfirmationRequest({self.product.name} - "
            f"{self.shop.name} [{self.status}])"
        )
