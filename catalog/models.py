from pathlib import Path
from uuid import uuid4

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import UploadedFile
from django.core.validators import FileExtensionValidator
from django.db import models
from django.db.models.signals import post_delete
from django.dispatch import receiver
from django.utils.text import slugify

from core.models import BaseModel

from .image_utils import validate_image_file


class ConfirmationStatus(models.TextChoices):
    """Whether our stock data for a product is still trustworthy.

    Orthogonal to `Product.is_available`: `is_available` is what the merchant
    last told us, `confirmation_status` is how much we still believe it.
    """

    FRESH = "fresh", "Fresh"
    STALE = "stale", "Stale"


class Category(BaseModel):
    name = models.CharField(max_length=100)
    slug = models.SlugField(unique=True)
    parent = models.ForeignKey(
        "self", null=True, blank=True, related_name="children", on_delete=models.PROTECT
    )
    display_order = models.PositiveIntegerField(default=0)

    class Meta(BaseModel.Meta):
        ordering = ["display_order", "name"]
        verbose_name_plural = "categories"

    def __str__(self) -> str:
        return self.name

    def clean(self) -> None:
        if self.parent_id and self.parent_id == self.id:
            raise ValidationError({"parent": "A category cannot be its own parent."})
        if self.parent and self.parent.parent_id:
            raise ValidationError(
                {"parent": "Categories can only be nested one level deep."}
            )


class ProductQuerySet(models.QuerySet):
    """The one definition of the confirmation clock.

    `sweep_freshness` and the bot's /myproducts listing must agree exactly about
    which products need a merchant's answer. Two copies of this predicate would
    let them drift apart silently -- the bot would prompt for one set while the
    API's ?freshness= filter reported another.
    """

    def needing_confirmation(self, cutoff):
        """Available products whose stock claim is no longer backed by evidence."""
        return self.filter(is_available=True).filter(
            models.Q(last_confirmed_at__isnull=True)
            | models.Q(last_confirmed_at__lt=cutoff)
        )

    def confirmed_since(self, cutoff):
        """The exact complement: NULLs fall out of a __gte comparison already."""
        return self.filter(is_available=True, last_confirmed_at__gte=cutoff)


class Product(BaseModel):
    shop = models.ForeignKey(
        "shops.Shop", related_name="products", on_delete=models.CASCADE
    )
    category = models.ForeignKey(
        Category, related_name="products", on_delete=models.PROTECT
    )
    description = models.TextField(blank=True, null=True)
    brand = models.CharField(max_length=100)
    name = models.CharField(max_length=200)
    slug = models.SlugField(max_length=200, unique=True, blank=True)
    price = models.DecimalField(max_digits=10, decimal_places=2)
    currency = models.CharField(max_length=3, default="ETB")
    last_confirmed_at = models.DateTimeField(null=True, blank=True)
    confirmation_status = models.CharField(
        max_length=20, choices=ConfirmationStatus.choices, default="fresh"
    )
    is_available = models.BooleanField(default=True)
    specs = models.JSONField(default=dict, blank=True)

    objects = ProductQuerySet.as_manager()

    def __str__(self) -> str:
        return f"{self.brand} {self.name}"

    def save(self, *args, **kwargs):
        if not self.slug:
            # Same shop reposting the same item must not collide on the unique
            # slug: `shop-brand-name`, then `-2`, `-3`, ... like Shop.save().
            base = slugify(f"{self.shop.slug}-{self.brand}-{self.name}")
            slug = base
            counter = 1
            while Product.objects.filter(slug=slug).exclude(pk=self.pk).exists():
                counter += 1
                slug = f"{base}-{counter}"
            self.slug = slug
        super().save(*args, **kwargs)


def product_image_path(instance, filename):
    ext = Path(filename).suffix.lower()
    return f"product_image/{uuid4().hex}{ext}"


class ProductImage(BaseModel):
    product = models.ForeignKey(
        Product, on_delete=models.CASCADE, related_name="product_image"
    )
    image = models.ImageField(
        upload_to=product_image_path,
        validators=[FileExtensionValidator(["jpg", "jpeg", "png", "webp"])],
    )
    display_order = models.PositiveBigIntegerField(default=0)
    is_primary = models.BooleanField(default=False)

    class Meta(BaseModel.Meta):
        constraints = [
            models.UniqueConstraint(
                fields=["product"],
                condition=models.Q(is_primary=True),
                name="unique_primary_image_per_product",
            )
        ]

    def clean(self):
        if self.image:
            validate_image_file(self.image)

    def save(self, *args, **kwargs):
        if self.image and isinstance(getattr(self.image, "file", None), UploadedFile):
            validate_image_file(self.image)
        super().save(*args, **kwargs)


class ConfirmationRequest(BaseModel):
    STATUS = [
        ("pending", "Pending"),
        ("confirmed", "Confirmed"),
        ("sold_out", "Sold_out"),
        ("expired", "Expired"),
    ]

    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    shop = models.ForeignKey("shops.Shop", on_delete=models.PROTECT)
    sent_at = models.DateTimeField(auto_now_add=True)
    responded_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=100, choices=STATUS, default="pending")
    channel = models.CharField(max_length=100, default="telegram")

    class Meta:
        ordering = ["responded_at", "sent_at"]


@receiver(post_delete, sender=ProductImage)
def delete_image_file(sender, instance, **kwargs):
    if instance.image:
        instance.image.delete(save=False)
