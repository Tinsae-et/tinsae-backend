from decimal import Decimal, InvalidOperation

from django.db.models import Case, IntegerField, Prefetch, Q, Value, When
from django.db.models.functions import Coalesce
from rest_framework import viewsets
from rest_framework.exceptions import ValidationError

from .models import Category, ConfirmationStatus, Product, ProductImage
from .serializers import CategorySerializer, ProductSerializer

ORDERING_FIELDS = {
    "name",
    "price",
    "brand",
    "slug",
    "created_at",
    "updated_at",
    "last_confirmed_at",
}

TRUTHY = {"1", "true", "yes", "on"}


# `Product.price` is max_digits=10, decimal_places=2, so nothing at or above
# this can be stored -- and a filter bound that large is a number Postgres has
# to be protected from rather than a query worth running. See `price_param`.
PRICE_LIMIT = Decimal("1e8")


def price_param(params, name):
    """Coerce a price filter to a Decimal, or reject it as a field error.

    Handing the raw string to `price__gte` lets DecimalField raise Django's own
    `ValidationError` from inside query construction. DRF's exception handler
    only translates `rest_framework.exceptions.ValidationError`, so that one
    escapes the view as an unhandled 500 -- an unparseable query string is a
    client error, and anyone could trigger it from a URL bar. Checked here for
    the same reason `ordering` is checked against an allowlist below.

    Parsing alone is not enough, which is the non-obvious part. "nan",
    "Infinity" and "1e999999" are all valid Decimal literals: they clear the
    parse and fail later and worse. NaN compares false against every row, so the
    endpoint quietly returns nothing; an exponent past the numeric format
    overflows inside the driver, which is another 500 by a different route.
    """
    raw = params.get(name)
    if not raw:
        return None
    try:
        value = Decimal(raw)
    except InvalidOperation:
        value = None
    if value is None or not value.is_finite() or abs(value) >= PRICE_LIMIT:
        raise ValidationError({name: f"Not a valid price: {raw}."})
    return value


class CategoryViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Category.objects.all()
    serializer_class = CategorySerializer


class ProductViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = ProductSerializer
    lookup_field = "slug"

    def get_queryset(self):
        qs = (
            Product.objects.filter(is_available=True)
            .select_related("category", "shop")
            # Sorted here rather than in the serializer: calling .order_by() on
            # the prefetched manager discards the prefetch and costs one extra
            # query per product on every list page.
            .prefetch_related(
                Prefetch(
                    "product_image",
                    queryset=ProductImage.objects.order_by(
                        "-is_primary", "display_order"
                    ),
                )
            )
            .annotate(
                # Three tiers, best first. A product the merchant confirmed 40
                # days ago still carries more signal than one they have never
                # confirmed at all, so the NULL check has to come before the
                # status check -- `stale` and `unconfirmed` both fall to the
                # middle tier, and this pulls the never-confirmed ones below
                # the once-confirmed ones.
                freshness_rank=Case(
                    When(last_confirmed_at__isnull=True, then=Value(2)),
                    When(confirmation_status=ConfirmationStatus.FRESH, then=Value(0)),
                    default=Value(1),
                    output_field=IntegerField(),
                ),
                last_signal=Coalesce("last_confirmed_at", "created_at"),
            )
        )

        ordering = self.request.query_params.get("ordering")
        if ordering:
            field = ordering[1:] if ordering.startswith("-") else ordering
            if field not in ORDERING_FIELDS:
                raise ValidationError(
                    {"ordering": f"Invalid ordering field: {ordering}."}
                )

        if q := self.request.query_params.get("q"):
            qs = qs.filter(
                Q(name__icontains=q)
                | Q(description__icontains=q)
                | Q(brand__icontains=q)
            )
        min_price = price_param(self.request.query_params, "min_price")
        if min_price is not None:
            qs = qs.filter(price__gte=min_price)
        max_price = price_param(self.request.query_params, "max_price")
        if max_price is not None:
            qs = qs.filter(price__lte=max_price)
        if self.request.query_params.get("verified_only"):
            qs = qs.filter(shop__is_verified=True)
        if self.request.query_params.get("freshness"):
            # Read the column the sweep maintains rather than recomputing the
            # cutoff here — otherwise the API and `sweep_freshness` can disagree,
            # and a never-confirmed new listing gets wrongly excluded.
            qs = qs.filter(confirmation_status=ConfirmationStatus.FRESH)
        # Strict about its value, unlike the two flags above: those accept any
        # non-empty string and their tests pin that down, but `fresh=false` has
        # an obvious intended meaning and must not switch the filter on.
        if (self.request.query_params.get("fresh") or "").lower() in TRUTHY:
            qs = qs.fresh()
        if category := self.request.query_params.get("category"):
            qs = qs.filter(category__slug=category)

        # An explicit sort replaces the freshness ranking rather than nesting
        # underneath it: a buyer who asks for cheapest-first means it. Filters
        # compose with the ranking; sorts override it.
        if ordering:
            return qs.order_by(ordering)
        return qs.order_by("freshness_rank", "-last_signal")
