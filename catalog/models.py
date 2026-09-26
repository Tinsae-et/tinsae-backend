from pathlib import Path
from uuid import uuid4

from django.conf import settings
from django.contrib.postgres.indexes import GinIndex, OpClass
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import UploadedFile
from django.core.validators import FileExtensionValidator
from django.db import models
from django.db.models.functions import Upper
from django.db.models.signals import post_delete
from django.dispatch import receiver
from django.utils import timezone
from django.utils.text import slugify

from core.models import BaseModel
from core.slugs import save_with_unique_slug

from .freshness import (
    ALMOST_EXPIRED_WINDOW,
    FRESH_WINDOW,
    freshness_case,
    get_freshness,
)
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


class AttributeType(models.TextChoices):
    ENUM = "enum", "Enum"
    TEXT = "text", "Text"
    NUMBER = "number", "Number"


class Attribute(BaseModel):
    """One spec, defined once and reused by every product type that needs it.

    `color` is the same attribute on a phone and on a speaker, so a buyer filter
    or a compare row means the same thing everywhere. `key` is what is stored in
    `Product.specs`; it is never renamed, because every listing already holding
    it would silently lose the value. Definitions live in `catalog/taxonomy/`
    and reach the database through `manage.py sync_taxonomy`.
    """

    key = models.SlugField(max_length=50, unique=True)
    label = models.CharField(max_length=80)
    type = models.CharField(max_length=10, choices=AttributeType.choices)
    unit = models.CharField(max_length=10, blank=True)
    min_value = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True
    )
    max_value = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True
    )

    class Meta(BaseModel.Meta):
        ordering = ["key"]

    def __str__(self) -> str:
        return self.key


class AttributeOption(BaseModel):
    """An allowed value of an enum attribute: stored as `value`, shown as `label`.

    Retired options are deactivated rather than deleted -- older listings may
    still hold them, and deleting would turn a valid listing invalid.
    """

    attribute = models.ForeignKey(
        Attribute, related_name="options", on_delete=models.CASCADE
    )
    value = models.SlugField(max_length=50)
    label = models.CharField(max_length=80)
    display_order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta(BaseModel.Meta):
        ordering = ["attribute", "display_order"]
        constraints = [
            models.UniqueConstraint(
                fields=["attribute", "value"], name="unique_option_per_attribute"
            )
        ]

    def __str__(self) -> str:
        return f"{self.attribute.key}={self.value}"


class ProductType(BaseModel):
    """The spec set a category's listings must carry (smartphone, laptop...)."""

    key = models.SlugField(max_length=50, unique=True)
    label = models.CharField(max_length=80)
    # The brands the listing form offers. A list for now; a Brand model can
    # replace it once brands need their own pages or aliases.
    brands = models.JSONField(default=list, blank=True)

    class Meta(BaseModel.Meta):
        ordering = ["key"]

    def __str__(self) -> str:
        return self.label


class ProductTypeAttribute(BaseModel):
    """How one product type uses one attribute: rules, order and overrides.

    `options` narrows the attribute's values for this type (a phone offers
    64GB-1TB storage, a laptop 256GB-2TB); empty means every active option.
    `required_if` / `show_if` map other keys -- specs, or `condition` -- to the
    values that trigger them, e.g. {"condition": ["used"]}.
    """

    product_type = models.ForeignKey(
        ProductType, related_name="fields", on_delete=models.CASCADE
    )
    attribute = models.ForeignKey(
        Attribute, related_name="product_types", on_delete=models.PROTECT
    )
    label = models.CharField(max_length=80, blank=True)
    required = models.BooleanField(default=False)
    required_if = models.JSONField(null=True, blank=True)
    show_if = models.JSONField(null=True, blank=True)
    options = models.JSONField(default=list, blank=True)
    control = models.CharField(max_length=10, blank=True)
    # Where the field sits: key specs (what buyers filter on), details (the
    # spec sheet, collapsible) or item (facts about this unit: warranty,
    # battery health, lock). `recommended` fields do not block publishing;
    # they count toward the form's completeness meter and lead the product
    # page's key-spec card.
    section = models.CharField(
        max_length=10,
        choices=[("key", "Key"), ("details", "Details"), ("item", "This item")],
        default="key",
    )
    recommended = models.BooleanField(default=False)
    placeholder = models.CharField(max_length=80, blank=True)
    hint = models.CharField(max_length=160, blank=True)
    display_order = models.PositiveIntegerField(default=0)

    class Meta(BaseModel.Meta):
        ordering = ["product_type", "display_order"]
        constraints = [
            models.UniqueConstraint(
                fields=["product_type", "attribute"],
                name="unique_attribute_per_product_type",
            )
        ]

    def __str__(self) -> str:
        return f"{self.product_type.key}.{self.attribute.key}"


class Category(BaseModel):
    name = models.CharField(max_length=100)
    slug = models.SlugField(unique=True)
    parent = models.ForeignKey(
        "self", null=True, blank=True, related_name="children", on_delete=models.PROTECT
    )
    display_order = models.PositiveIntegerField(default=0)
    # Decides the listing form and the spec validation for this category. A
    # category without one takes listings with free-form specs, as before.
    product_type = models.ForeignKey(
        ProductType,
        null=True,
        blank=True,
        related_name="categories",
        on_delete=models.PROTECT,
    )

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
    which products need a merchant's answer. The buyer-facing API filter is
    `?fresh=` and uses the live confirmation timestamp below.
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

    def buyer_visible(self, now=None):
        """Available, unexpired listings visible to buyers.

        Excludes listings that are paused (is_available=False or draft/sold)
        and listings that have expired past ALMOST_EXPIRED_WINDOW.
        """
        now = now or timezone.now()
        return self.filter(
            is_available=True,
            status=ProductStatus.LIVE,
            last_confirmed_at__gte=now - ALMOST_EXPIRED_WINDOW,
        )

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
                OpClass(Upper("name"), name="gin_trgm_ops"),
                name="product_name_upper_trgm",
            ),
            GinIndex(
                OpClass(Upper("brand"), name="gin_trgm_ops"),
                name="product_brand_upper_trgm",
            ),
            GinIndex(
                OpClass(Upper("description"), name="gin_trgm_ops"),
                name="product_desc_upper_trgm",
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
    # Seeding reuses one file across many rows, so a file is removed only when
    # no remaining image still points at it -- otherwise deleting one listing
    # would break the photos of every other listing sharing that file.
    if not instance.image:
        return
    if ProductImage.objects.filter(image=instance.image.name).exists():
        return
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
