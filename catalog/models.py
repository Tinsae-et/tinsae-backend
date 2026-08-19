from pathlib import Path
from uuid import uuid4

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import UploadedFile
from django.core.validators import FileExtensionValidator
from django.db import models
from django.db.models.signals import post_delete
from django.dispatch import receiver

from core.models import BaseModel

from .image_utils import validate_image_file


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
    slug = models.SlugField(max_length=200, unique=True)
    price = models.DecimalField(max_digits=10, decimal_places=2)
    currency = models.CharField(max_length=3, default="ETB")
    last_confirmed_at = models.DateTimeField(null=True, blank=True)
    is_available = models.BooleanField(default=True)
    specs = models.JSONField(default=dict, blank=True)

    def __str__(self) -> str:
        return f"{self.brand} {self.name}"


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
