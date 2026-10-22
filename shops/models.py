from decimal import Decimal

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver
from django.utils.text import slugify

from catalog.image_utils import validate_image_file
from core.models import BaseModel
from core.slugs import save_with_unique_slug


class Shop(BaseModel):
    name = models.CharField(max_length=200)
    slug = models.SlugField(max_length=220, unique=True, blank=True)
    description = models.TextField(blank=True)
    logo = models.ImageField(
        upload_to="shops/logos/",
        blank=True,
        null=True,
        validators=[validate_image_file],
    )
    phone = models.CharField(max_length=30, blank=True)
    city = models.CharField(max_length=100, blank=True)
    address = models.CharField(max_length=200, blank=True)
    latitude = models.DecimalField(
        max_digits=9,
        decimal_places=6,
        null=True,
        blank=True,
        validators=[
            MinValueValidator(Decimal("-90.0")),
            MaxValueValidator(Decimal("90.0")),
        ],
    )
    longitude = models.DecimalField(
        max_digits=9,
        decimal_places=6,
        null=True,
        blank=True,
        validators=[
            MinValueValidator(Decimal("-180.0")),
            MaxValueValidator(Decimal("180.0")),
        ],
    )
    telegram_chat_id = models.CharField(max_length=50, blank=True)
    telegram_username = models.CharField(
        max_length=100,
        blank=True,
        help_text="Public Telegram handle (e.g. abebe_shop) for direct buyer messaging.",
    )
    blocked_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When Telegram reported a 403 Forbidden (bot blocked by user).",
    )
    language = models.CharField(
        max_length=10,
        default="en",
        help_text="Preferred language for bot messages (e.g. en, am).",
    )
    is_verified = models.BooleanField(default=False)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="shops",
    )
    categories = models.ManyToManyField(
        "catalog.Category", blank=True, related_name="shops"
    )

    class Meta(BaseModel.Meta):
        ordering = ["name"]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if self.telegram_username:
            val = self.telegram_username.strip()
            for prefix in ("https://t.me/", "http://t.me/", "t.me/", "@"):
                if val.startswith(prefix):
                    val = val[len(prefix) :]
            self.telegram_username = val.strip("/").strip()
        if self.slug:
            return super().save(*args, **kwargs)
        # slugify() strips non-Latin scripts entirely, so an Amharic-only name
        # slugifies to "" and the series in core.slugs becomes "", "-2", "-3".
        # A generic ASCII base keeps every shop routable; the slug is an
        # identifier, not a label.
        base = slugify(self.name) or "shop"
        return save_with_unique_slug(self, base, super().save, *args, **kwargs)


class ShopPhoto(BaseModel):
    shop = models.ForeignKey(Shop, on_delete=models.CASCADE, related_name="photos")
    image = models.ImageField(
        upload_to="shops/photos/", validators=[validate_image_file]
    )
    display_order = models.PositiveIntegerField(default=0)
    is_cover = models.BooleanField(default=False)

    class Meta(BaseModel.Meta):
        ordering = ["-is_cover", "display_order", "created_at"]

    def __str__(self):
        return f"{self.shop.slug} photo {self.pk}"


class VerificationRequest(models.Model):
    STATES = [
        ("pending", "Pending"),
        ("approved", "Approved"),
        ("rejected", "Rejected"),
    ]

    shop = models.ForeignKey(Shop, on_delete=models.CASCADE)
    submitted_at = models.DateTimeField(auto_now_add=True)
    reviewed_at = models.DateTimeField(auto_now=True)
    reviewer = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True
    )
    state = models.CharField(max_length=10, choices=STATES, default="pending")
    rejection_reason = models.TextField(blank=True, null=True)
    supporting_detail = models.TextField(null=True, blank=True)


@receiver(post_save, sender=VerificationRequest)
def post_update_shop_verification_status(sender, instance, **kwargs):
    if instance.state == "approved":
        if not instance.shop.is_verified:
            instance.shop.is_verified = True
            instance.shop.save(update_fields=["is_verified"])
    elif instance.state in ("rejected", "pending"):
        if instance.shop.is_verified:
            instance.shop.is_verified = False
            instance.shop.save(update_fields=["is_verified"])

    if instance.state in ("approved", "rejected"):
        from notifications.services import notify

        notify(
            instance.shop.owner,
            "verification_reviewed",
            {
                "shop_id": instance.shop_id,
                "shop_slug": instance.shop.slug,
                "request_id": instance.pk,
                "state": instance.state,
            },
        )


@receiver(pre_save, sender=VerificationRequest)
def pre_update_shop_verification_status(sender, instance, **kwargs):
    if instance.state == "rejected" and not (instance.rejection_reason or "").strip():
        instance.rejection_reason = "No reason provided."
