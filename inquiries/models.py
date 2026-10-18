from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone

from core.models import BaseModel

MAX_BODY_LENGTH = 1000


class Inquiry(BaseModel):
    """One conversation between a buyer and a shop, optionally about a product.

    State changes go through `inquiries.services`; reads that must respect
    who is asking go through `inquiries.selectors`.
    """

    class Status(models.TextChoices):
        OPEN = "open", "Open"
        CLOSED = "closed", "Closed"

    buyer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="buyer_inquiries",
    )
    shop = models.ForeignKey(
        "shops.Shop", on_delete=models.CASCADE, related_name="shop_inquiries"
    )
    # SET_NULL keeps the conversation when a listing is deleted; the history
    # belongs to the buyer and the shop, not to the product.
    product = models.ForeignKey(
        "catalog.Product",
        on_delete=models.SET_NULL,
        related_name="product_inquiries",
        blank=True,
        null=True,
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.OPEN,
    )
    last_message_at = models.DateTimeField(default=timezone.now)
    buyer_unread = models.PositiveIntegerField(default=0)
    shop_unread = models.PositiveIntegerField(default=0)

    class Meta(BaseModel.Meta):
        ordering = ["-last_message_at"]
        indexes = [
            models.Index(
                fields=["buyer", "-last_message_at"], name="inquiry_buyer_inbox_idx"
            ),
            models.Index(
                fields=["shop", "-last_message_at"], name="inquiry_shop_inbox_idx"
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["buyer", "shop", "product"],
                condition=Q(status="open", product__isnull=False),
                name="unique_open_inquiry_per_buyer_shop_product",
            ),
            models.UniqueConstraint(
                fields=["buyer", "shop"],
                condition=Q(status="open", product__isnull=True),
                name="unique_open_inquiry_per_buyer_shop_without_product",
            ),
        ]

    @property
    def is_open(self):
        return self.status == self.Status.OPEN

    def __str__(self):
        product_text = f" / {self.product}" if self.product else ""
        return f"Inquiry {self.buyer_id}-{self.shop_id}{product_text} ({self.status})"


class InquiryMessage(BaseModel):
    class SenderRole(models.TextChoices):
        BUYER = "buyer", "Buyer"
        SHOP = "shop", "Shop"

    class SentVia(models.TextChoices):
        WEB = "web", "Web"
        TELEGRAM = "telegram", "Telegram"

    inquiry = models.ForeignKey(
        Inquiry, on_delete=models.CASCADE, related_name="messages"
    )
    sender_role = models.CharField(max_length=20, choices=SenderRole.choices)
    body = models.CharField(max_length=MAX_BODY_LENGTH)
    sent_via = models.CharField(max_length=20, choices=SentVia.choices)

    class Meta(BaseModel.Meta):
        # A thread reads top to bottom.
        ordering = ["created_at"]

    def __str__(self):
        return f"{self.sender_role}: {self.body[:50]}"
