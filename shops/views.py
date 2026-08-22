from django.shortcuts import get_object_or_404
from rest_framework import permissions, status, viewsets
from rest_framework.response import Response

from catalog.models import Category, Product

from .models import Shop
from .permissions import IsOwnerOrReadOnly
from .serializers import (
    ShopListSerializer,
    ShopProductSerializer,
    ShopPublicSerializer,
    ShopSerializer,
)


class ShopViewSet(viewsets.ModelViewSet):
    queryset = Shop.objects.all()
    lookup_field = "slug"
    permission_classes = [permissions.IsAuthenticatedOrReadOnly, IsOwnerOrReadOnly]

    def get_serializer_class(self):
        if self.action == "list":
            return ShopListSerializer
        if self.action == "retrieve" and not self._viewer_owns_shop():
            return ShopPublicSerializer
        return ShopSerializer

    def _viewer_owns_shop(self) -> bool:
        # Anonymous short-circuits before get_object(), so the common read path
        # costs no extra query.
        user = self.request.user
        if not user.is_authenticated:
            return False
        return user.is_staff or self.get_object().owner_id == user.pk

    def perform_create(self, serializer):
        serializer.save(owner=self.request.user)


class ManageMyProductsViewSet(viewsets.ModelViewSet):
    queryset = Product.objects.all()
    permission_classes = [IsOwnerOrReadOnly]
    serializer_class = ShopProductSerializer
    lookup_field = "slug"

    def create(self, request, *args, **kwargs):
        data = request.data.copy()
        data.pop("category_slug", None)
        serializer = self.get_serializer(data=data)
        serializer.is_valid(raise_exception=True)
        self.perform_create(serializer)
        headers = self.get_success_headers(serializer.data)
        return Response(
            serializer.data, status=status.HTTP_201_CREATED, headers=headers
        )

    def perform_create(self, serializer):
        serializer.save(
            shop=self.get_shop(),
            category=self.get_category(self.request.data.get("category_slug")),
        )

    def get_shop(self):
        return get_object_or_404(Shop, owner=self.request.user)

    def get_category(self, slug):
        return get_object_or_404(Category, slug=slug)
