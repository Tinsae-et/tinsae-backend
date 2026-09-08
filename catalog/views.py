import logging
from decimal import Decimal, InvalidOperation

from django.db.models import Case, Count, IntegerField, Prefetch, Q, Value, When
from django.db.models.functions import Coalesce
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views.decorators.cache import cache_page
from rest_framework import generics, permissions, views, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from core.pagination import ProductSuggestionsPagination

from shops.models import Shop

from .freshness import ALMOST_EXPIRED_WINDOW
from .models import Category, ConfirmationStatus, Product, ProductImage
from .serializers import CategorySerializer, ProductSerializer
from .services import apply_search, get_product_suggestions, log_search

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
PUBLIC_STATS_TTL = 60 * 60 * 24
logger = logging.getLogger(__name__)


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

        # Ranked search lives on this route because this is the route the
        # frontend calls (`frontend-implementation-guide.md`: "GET
        # /catalog/products/?q=..."). TIN-58 asked to upgrade the TIN-16 search,
        # and a second endpoint beside it would have been a fork, not an
        # upgrade: two ranking rules, and only one of them wired to a client.
        if query := (self.request.query_params.get("q") or "").strip():
            qs = apply_search(qs, query)
        min_price = price_param(self.request.query_params, "min_price")
        if min_price is not None:
            qs = qs.filter(price__gte=min_price)
        max_price = price_param(self.request.query_params, "max_price")
        if max_price is not None:
            qs = qs.filter(price__lte=max_price)
        if self.request.query_params.get("verified_only"):
            qs = qs.filter(shop__is_verified=True)
        if "freshness" in self.request.query_params:
            logger.warning(
                "Deprecated query parameter ?freshness= used on the public "
                "product list; use ?fresh= instead. Remove after 2026-12-31."
            )
        # Strict about its value, unlike the two flags above: those accept any
        # non-empty string and their tests pin that down, but `fresh=false` has
        # an obvious intended meaning and must not switch the filter on.
        if (self.request.query_params.get("fresh") or "").lower() in TRUTHY:
            qs = qs.fresh()
        if category := self.request.query_params.get("category"):
            qs = qs.filter(category__slug=category)

        # An explicit sort replaces the ranking rather than nesting underneath
        # it: a buyer who asks for cheapest-first means it. Filters compose with
        # the ranking; sorts override it.
        if ordering:
            return qs.order_by(ordering)
        if query:
            # `apply_search` already ordered by relevance. Re-sorting on
            # freshness here would undo the entire ranking.
            return qs
        return qs.order_by("freshness_rank", "-last_signal")

    def list(self, request, *args, **kwargs):
        """Serve the page, then record the search that produced it.

        After `super()`, not inside `get_queryset`, for two reasons: the count
        is only real once the page has been built, and `get_queryset` is not a
        place with side effects -- DRF is free to call it more than once per
        request, and every extra call would be a duplicate row.
        """
        response = super().list(request, *args, **kwargs)
        if query := (request.query_params.get("q") or "").strip():
            log_search(query, response.data.get("count", 0), request.user)
        return response

    @action(detail=True, methods=["get"], url_path="suggestions")
    def suggestions(self, request, slug=None):
        """Serve tiered product suggestions locked strictly to the same category.

        Evaluated in a single SQL query via PostgreSQL conditional CASE expressions:
          - Tier 1: Same Brand + Close Price (±30%)
          - Tier 2: Competitors in Close Price Bracket (±30%)
          - Tier 3: Same Brand (other prices)
          - Tier 4: Other category items
        Ranked internally by stock freshness, shop verification, city proximity, and recency.
        """
        product = get_object_or_404(
            Product.objects.select_related("category", "shop"), slug=slug
        )

        qs = get_product_suggestions(product)

        paginator = ProductSuggestionsPagination()
        page = paginator.paginate_queryset(qs, request, view=self)
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            return paginator.get_paginated_response(serializer.data)

        serializer = self.get_serializer(qs, many=True)
        return Response(serializer.data)


# `ProductSearchViewSet` was removed here. It was a second search endpoint at
# /catalog/productsearch/ with its own ranking, no price/category/freshness
# filters, and no client -- the frontend has always called /catalog/products/?q=.
# It was also a plain `ModelViewSet`, which under the project default of
# `IsAuthenticatedOrReadOnly` exposed PUT, PATCH and DELETE on products to every
# account that could log in. Its ranking now lives in `ProductViewSet` above.


class SimilarProductsView(generics.ListAPIView):
    serializer_class = ProductSerializer

    def get_product(self):
        slug = self.kwargs.get("slug")
        return get_object_or_404(Product, slug=slug)

    def get_queryset(self):
        product = self.get_product()
        # Price bounds are 20% above and below the product's price.
        upper_price_bound = product.price * Decimal("1.2")
        lower_price_bound = product.price * Decimal("0.8")

        brand_query = Q(brand=product.brand) if product.brand else Q()

        qs = (
            Product.objects.buyer_visible()
            .filter(
                Q(category=product.category)
                & (Q(price__range=(lower_price_bound, upper_price_bound)) | brand_query)
            )
            .exclude(id=product.id)
            .select_related("category", "shop")
            .prefetch_related(
                Prefetch(
                    "product_image",
                    queryset=ProductImage.objects.order_by(
                        "-is_primary", "display_order"
                    ),
                )
            )
            .annotate(
                same_shop_rank=Case(
                    When(shop=product.shop, then=Value(1)),
                    default=Value(0),
                    output_field=IntegerField(),
                ),
                freshness_rank=Case(
                    When(last_confirmed_at__isnull=True, then=Value(2)),
                    When(confirmation_status=ConfirmationStatus.FRESH, then=Value(0)),
                    default=Value(1),
                    output_field=IntegerField(),
                ),
                last_signal=Coalesce("last_confirmed_at", "created_at"),
            )
        )

        return qs.order_by("same_shop_rank", "freshness_rank", "-last_signal")[:4]


class PublicStatsView(views.APIView):
    permission_classes = [permissions.AllowAny]
    throttle_scope = "public_stats"

    @method_decorator(cache_page(PUBLIC_STATS_TTL))
    def get(self, request, *args, **kwargs):
        buyer_visible_qs = Product.objects.buyer_visible()

        total_shops = Shop.objects.count()
        verified_shops = Shop.objects.filter(is_verified=True).count()
        total_listings = buyer_visible_qs.count()
        fresh_listings = buyer_visible_qs.fresh().count()
        verified_shop_products = buyer_visible_qs.filter(shop__is_verified=True).count()

        category_counts = (
            buyer_visible_qs.values("category__slug")
            .annotate(count=Count("id"))
            .order_by()
        )
        category_product_count = {
            row["category__slug"]: row["count"]
            for row in category_counts
            if row["category__slug"]
        }

        data = {
            "total_shops": total_shops,
            "total_listings": total_listings,
            "fresh_listings": fresh_listings,
            "total_products": total_listings,
            "verified_shops": verified_shops,
            "verified_shop_products": verified_shop_products,
            "category_product_count": category_product_count,
        }
        return Response(data)
