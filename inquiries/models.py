from django.contrib.auth import get_user_model
from django.db import models
from django.db.models import Q
from django.utils import timezone

from catalog.models import Product
from core.models import BaseModel
from shops.models import Shop

User = get_user_model()


class Inquiry(BaseModel):
    class Status(models.TextChoices):
        OPEN = "open", "Open"
        CLOSED = "closed", "Closed"

    buyer = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="buyer_inquiries"
    )
    shop = models.ForeignKey(
        Shop, on_delete=models.CASCADE, related_name="shop_inquiries"
    )
    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
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

    @classmethod
    def get_or_create_open(cls, buyer, shop, product=None, **kwargs):
        queryset = cls.objects.filter(buyer=buyer, shop=shop)
        if product is not None:
            queryset = queryset.filter(product=product)
        else:
            queryset = queryset.filter(product__isnull=True)

        inquiry = (
            queryset.filter(status=cls.Status.OPEN).order_by("-updated_at").first()
        )
        if inquiry is not None:
            return inquiry

        inquiry = (
            queryset.filter(status=cls.Status.CLOSED).order_by("-updated_at").first()
        )
        if inquiry is not None:
            return inquiry.reopen()

        data = {
            "buyer": buyer,
            "shop": shop,
            "status": cls.Status.OPEN,
            "last_message_at": timezone.now(),
            **kwargs,
        }
        if product is not None:
            data["product"] = product
        return cls.objects.create(**data)

    open_or_create = get_or_create_open

    def reopen(self):
        if self.status != self.Status.OPEN:
            self.status = self.Status.OPEN
            self.save(update_fields=["status", "updated_at"])
        return self

    def close(self):
        if self.status != self.Status.CLOSED:
            self.status = self.Status.CLOSED
            self.save(update_fields=["status", "updated_at"])
        return self

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
    body = models.CharField(max_length=1000)
    sent_via = models.CharField(max_length=20, choices=SentVia.choices)

    def __str__(self):
        return f"{self.sender_role}: {self.body[:50]}"
