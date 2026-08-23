from django.db.models import Case, IntegerField, Prefetch, Q, Value, When
from django.db.models.functions import Coalesce
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from .freshness import get_freshness
from .models import Category, ConfirmationStatus, Product, ProductImage
from .serializers import CategorySerializer, ProductImageSerializer, ProductSerializer

ORDERING_FIELDS = {
    "name",
    "price",
    "brand",
    "slug",
    "created_at",
    "updated_at",
    "last_confirmed_at",
}


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
                # status check -- the sweep marks both "stale", and this pulls
                # the never-confirmed ones below the once-confirmed ones.
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
        if min_price := self.request.query_params.get("min_price"):
            qs = qs.filter(price__gte=min_price)
        if max_price := self.request.query_params.get("max_price"):
            qs = qs.filter(price__lte=max_price)
        if self.request.query_params.get("verified_only"):
            qs = qs.filter(shop__is_verified=True)
        if self.request.query_params.get("freshness"):
            # Read the column the sweep maintains rather than recomputing the
            # cutoff here — otherwise the API and `sweep_freshness` can disagree,
            # and a never-confirmed new listing gets wrongly excluded.
            qs = qs.filter(confirmation_status=ConfirmationStatus.FRESH)
        if category := self.request.query_params.get("category"):
            qs = qs.filter(category__slug=category)

        # An explicit sort replaces the freshness ranking rather than nesting
        # underneath it: a buyer who asks for cheapest-first means it. Filters
        # compose with the ranking; sorts override it.
        if ordering:
            return qs.order_by(ordering)
        return qs.order_by("freshness_rank", "-last_signal")


class ProductImageViewSet(viewsets.ModelViewSet):
    queryset = ProductImage.objects.all()
    serializer_class = ProductImageSerializer

    def get_object(self):
        return Product.objects.get(pk=self.kwargs["pk"])

    def perform_create(self, serializer):
        serializer.save(product=self.get_object())


class ProductfreshnessViewSet(viewsets.ModelViewSet):
    queryset = Product.objects.all()
    serializer_class = ProductSerializer

    @action(detail=True, methods=["get"])
    def freshness(self, request, pk=None):
        product = Product.objects.get(pk=self.kwargs["pk"])
        fresh = get_freshness(product.last_confirmed_at)
        return Response({"freshness": fresh})
