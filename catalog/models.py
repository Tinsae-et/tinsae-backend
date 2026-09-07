from pathlib import Path
from uuid import uuid4

from django.conf import settings
from django.contrib.postgres.indexes import GinIndex
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import UploadedFile
from django.core.validators import FileExtensionValidator
from django.db import models
from django.db.models.signals import post_delete
from django.dispatch import receiver
from django.utils import timezone
from django.utils.text import slugify

from core.models import BaseModel
from core.slugs import save_with_unique_slug

from .freshness import FRESH_WINDOW, freshness_case, get_freshness
from .image_utils import validate_image_file


class ConfirmationStatus(models.TextChoices):
    """Whether our stock data for a product is still trustworthy.

    Orthogonal to `Product.is_available`: `is_available` is what the merchant
    last told us, `confirmation_status` is how much we still believe it.

    This column exists because prompting is a workflow with memory (who have we
    already asked, and did they answer?) and a workflow cannot be recomputed
    from a timestamp alone. The three values are a ladder, worst last:

        FRESH        answered inside the window
        STALE        past the window, due to be asked
        UNCONFIRMED  asked, and the merchant never answered

    STALE and UNCONFIRMED both mean "past the clock", so anything measuring
    catalogue health has to count them together -- see `ops.metrics`. What
    separates them is whether we have evidence the merchant is disengaged, which
    is the difference between "prompt them" and "follow up with them".

    This is the buyer-facing label. `catalog/freshness.py` derives a finer tier
    from `last_confirmed_at` on a shorter clock, and that one is now kept for
    ranking only: shipping both to buyers meant one payload could call the same
    product fresh and expired at once.
    """

    FRESH = "fresh", "Fresh"
    STALE = "stale", "Stale"
    UNCONFIRMED = "unconfirmed", "Unconfirmed"


class ProductCondition(models.TextChoices):
    """The physical condition of a listed product.

    Referenced by the dashboard listing form and the product comparison view.
    Stored on `Product.condition` and defaults to `NEW`.
    """

    NEW = "new", "New"
    USED = "used", "Used"
    REFURBISHED = "refurbished", "Refurbished"


Condition = ProductCondition


class ProductStatus(models.TextChoices):
    """The listing state of a product.

    LIVE: Active and visible to buyers.
    DRAFT: Incomplete or unpublished by merchant.
    SOLD: Marked sold out.
    """

    LIVE = "live", "Live"
    DRAFT = "draft", "Draft"
    SOLD = "sold", "Sold"


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

    def fresh(self, now=None):
        """Products still inside the fresh window.

        A pure freshness predicate, unlike the two above -- those encode the
        sweep's whole policy, availability included. Compose this with
        `.filter(is_available=True)` when you want both, so that asking for
        "fresh but sold out" stays possible.
        """
        now = now or timezone.now()
        return self.filter(last_confirmed_at__gte=now - FRESH_WINDOW)

    def with_freshness(self, now=None):
        """Annotate the derived label so it can be filtered and ordered in SQL.

        The alias is `freshness_label`, not `freshness`: the latter is a property
        on the model, and Django assigns annotations with `setattr` at iteration
        time, so shadowing it would raise on a property with no setter -- when
        the queryset is consumed, not when it is built.
        """
        return self.annotate(freshness_label=freshness_case(now))


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
    condition = models.CharField(
        max_length=20,
        choices=ProductCondition.choices,
        default=ProductCondition.NEW,
    )
    last_confirmed_at = models.DateTimeField(null=True, blank=True)
    confirmation_status = models.CharField(
        max_length=20, choices=ConfirmationStatus.choices, default="fresh"
    )
    status = models.CharField(
        max_length=20,
        choices=ProductStatus.choices,
        default=ProductStatus.LIVE,
    )
    stock = models.PositiveIntegerField(default=1)
    views = models.PositiveIntegerField(default=0)
    inquiries = models.PositiveIntegerField(default=0)
    is_available = models.BooleanField(default=True)
    specs = models.JSONField(default=dict, blank=True)

    objects = ProductQuerySet.as_manager()

    class Meta(BaseModel.Meta):
        # Every read of this column is a range scan against the clock --
        # `fresh`, `needing_confirmation`, `confirmed_since`, and the API's
        # ?ordering=last_confirmed_at all narrow on it.
        indexes = [
            models.Index(
                fields=["last_confirmed_at"], name="product_last_confirmed_idx"
            ),
            # `icontains` and `similarity()` both scan every row without these:
            # a leading-wildcard LIKE cannot use a btree index, and trigram
            # similarity has nothing to prune against. GIN + gin_trgm_ops serves
            # both, which is the main argument for keeping search on trigram
            # rather than moving to tsvector -- see docs/decisions.md, D8.
            GinIndex(
                fields=["name"], name="product_name_trgm", opclasses=["gin_trgm_ops"]
            ),
            GinIndex(
                fields=["brand"], name="product_brand_trgm", opclasses=["gin_trgm_ops"]
            ),
        ]

    @property
    def freshness(self):
        """The buyer-facing label, always computed against the live clock.

        Use `ProductQuerySet.with_freshness()` instead when the label needs to
        be filtered or sorted -- this evaluates in Python, one product at a time.
        """
        return get_freshness(self.last_confirmed_at)

    def __str__(self) -> str:
        return f"{self.brand} {self.name}"

    def save(self, *args, **kwargs):
        if self.status in (ProductStatus.SOLD, ProductStatus.DRAFT):
            self.is_available = False
        if self.slug:
            return super().save(*args, **kwargs)
        # Same shop reposting the same item must not collide on the unique
        # slug: `shop-brand-name`, then `-2`, `-3`, ... like Shop.save().
        base = slugify(f"{self.shop.slug}-{self.brand}-{self.name}") or "product"
        return save_with_unique_slug(self, base, super().save, *args, **kwargs)


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


@receiver(post_delete, sender=ProductImage)
def delete_image_file(sender, instance, **kwargs):
    if instance.image:
        instance.image.delete(save=False)


class SearchLog(BaseModel):
    """One buyer search, recorded so ranking can be tuned against real traffic.

    TIN-58 asks for ranking "tuned on real query logs" and a search-backend
    decision made "with data, not ahead of it". Neither is answerable without
    this table, which is why it lands before the tuning rather than after: the
    weights in `catalog.services` and the synonym list are both provisional
    guesses until there are weeks of rows here to check them against.

    The `result_count=0` rows are the point. A query that returns nothing is a
    buyer telling us exactly which word we failed to understand -- a missing
    synonym, a brand spelled the way people actually spell it, or an Amharic
    term the catalogue has no alias for. That list is the input to the alias
    work, and it is why `normalized` is indexed: the query that matters is
    "most frequent normalized queries with no results".

    Not an analytics event stream. No click-through, no session, no result
    slugs. Those need a frontend contract and a retention policy of their own;
    this is the smallest thing that unblocks the two blocked criteria.
    """

    raw_query = models.CharField(max_length=200)
    # Whitespace-collapsed and lowercased, so "  Samsung  PHONE " and
    # "samsung phone" aggregate into one row when counting. `raw_query` is kept
    # beside it because the exact keystrokes are the evidence for typo work --
    # normalising them away would discard the thing we want to measure.
    normalized = models.CharField(max_length=200, db_index=True)
    result_count = models.PositiveIntegerField()
    # Nullable and SET_NULL: most searches are anonymous, and a buyer deleting
    # their account must not delete the traffic history the ranking is tuned on.
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="search_logs",
    )

    class Meta(BaseModel.Meta):
        verbose_name_plural = "search logs"
        indexes = [
            models.Index(
                fields=["result_count", "-created_at"], name="searchlog_misses_idx"
            )
        ]

    def __str__(self):
        return f"{self.raw_query!r} -> {self.result_count}"
