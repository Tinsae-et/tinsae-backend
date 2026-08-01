from datetime import timedelta

from django.db.models import Q
from django.utils import timezone
from rest_framework import viewsets
from rest_framework.exceptions import ValidationError

from .models import Category, Product
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
            .prefetch_related("product_image")
        )
        if q := self.request.query_params.get("q"):
            qs = qs.filter(
                Q(name__icontains=q)
                | Q(description__icontains=q)
                | Q(brand__icontains=q)
            )
        if ordering := self.request.query_params.get("ordering"):
            field = ordering[1:] if ordering.startswith("-") else ordering
            if field not in ORDERING_FIELDS:
                raise ValidationError(
                    {"ordering": f"Invalid ordering field: {ordering}."}
                )
            qs = qs.order_by(ordering)
        if min_price := self.request.query_params.get("min_price"):
            qs = qs.filter(price__gte=min_price)
        if max_price := self.request.query_params.get("max_price"):
            qs = qs.filter(price__lte=max_price)
        if self.request.query_params.get("verified_only"):
            qs = qs.filter(shop__is_verified=True)
        if self.request.query_params.get("freshness"):
            qs = qs.filter(last_confirmed_at__gte=timezone.now() - timedelta(days=30))
        if category := self.request.query_params.get("category"):
            qs = qs.filter(category__slug=category)
        return qs
