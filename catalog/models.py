from django.core.exceptions import ValidationError
from django.db import models

from core.models import BaseModel


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
    confirmation_status = models.CharField(
        max_length=20, choices=ConfirmationStatus.choices, default="fresh"
    )
    is_available = models.BooleanField(default=True)
    image = models.URLField(max_length=500, blank=True)
    specs = models.JSONField(default=dict, blank=True)

    def __str__(self) -> str:
        return f"{self.brand} {self.name}"
