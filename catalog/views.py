from django.db.models import Case, IntegerField, Q, Value, When
from django.db.models.functions import Coalesce
from rest_framework import viewsets

from .models import Category, ConfirmationStatus, Product
from .serializers import CategorySerializer, ProductSerializer

# Sorts a client may ask for by name. Anything outside this set is ignored
# rather than handed to order_by(), which would raise FieldError and 500.
ALLOWED_ORDERING = frozenset(
    {
        "price",
        "-price",
        "name",
        "-name",
        "created_at",
        "-created_at",
        "last_confirmed_at",
        "-last_confirmed_at",
    }
)


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
            .annotate(
                # Three tiers, best first. A product the merchant confirmed 40
                # days ago still carries more signal than one they have never
                # confirmed at all, so the NULL check has to come before the
                # status check -- a never-confirmed product is "fresh" to the
                # sweep (it measures from created_at) but is not evidence.
                freshness_rank=Case(
                    When(last_confirmed_at__isnull=True, then=Value(2)),
                    When(confirmation_status=ConfirmationStatus.FRESH, then=Value(0)),
                    default=Value(1),
                    output_field=IntegerField(),
                ),
                last_signal=Coalesce("last_confirmed_at", "created_at"),
            )
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
        ordering = self.request.query_params.get("ordering")
        if ordering in ALLOWED_ORDERING:
            return qs.order_by(ordering)
        return qs.order_by("freshness_rank", "-last_signal")
