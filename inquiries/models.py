from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone

from core.models import BaseModel

MAX_BODY_LENGTH = 1000


class SenderRole(models.TextChoices):
    BUYER = "buyer", "Buyer"
    SHOP = "shop", "Shop"


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
    # Blank means "no message recorded since this field was added"; the reminder
    # job skips those, which is fine because of its 7-day lookback. No backfill.
    last_sender_role = models.CharField(
        max_length=20,
        choices=SenderRole.choices,
        blank=True,
        default="",
        db_default="",
    )
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
            models.Index(
                fields=["last_message_at"],
                condition=Q(status="open", last_sender_role="buyer"),
                name="inquiry_unanswered_idx",
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
    SenderRole = SenderRole

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


class ContactChannel(models.TextChoices):
    PHONE = "phone", "Phone"
    TELEGRAM = "telegram", "Telegram"
    IN_APP = "in_app", "In-App"


class ContactIntent(BaseModel):
    """A buyer's intent to contact a shop, tracked for conversion analytics.

    Unlike in-app conversations (Inquiry), this captures external hand-offs
    (phone calls and Telegram deep-links) as well as initial in-app message
    intents. Feeds merchant dashboard lead metrics and search/ranking analytics
    without exposing raw personal numbers to unauthenticated crawlers.
    """

    shop = models.ForeignKey(
        "shops.Shop",
        on_delete=models.CASCADE,
        related_name="contact_intents",
    )
    product = models.ForeignKey(
        "catalog.Product",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="contact_intents",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="contact_intents",
    )
    channel = models.CharField(max_length=20, choices=ContactChannel.choices)
    # Hashed client IP to track distinct lead counts without storing personal IP data.
    ip_hash = models.CharField(max_length=64, blank=True, db_index=True)
    user_agent = models.CharField(max_length=255, blank=True)

    class Meta(BaseModel.Meta):
        ordering = ["-created_at"]
        indexes = [
            models.Index(
                fields=["shop", "-created_at"], name="contact_intent_shop_idx"
            ),
            models.Index(
                fields=["product", "-created_at"], name="contact_intent_prod_idx"
            ),
            models.Index(
                fields=["channel", "-created_at"], name="contact_intent_chan_idx"
            ),
        ]

    def __str__(self):
        prod = f" / {self.product_id}" if self.product_id else ""
        return f"{self.channel} intent: shop {self.shop_id}{prod}"
