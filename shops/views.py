from django.shortcuts import get_object_or_404
from rest_framework import permissions, viewsets

from catalog.models import Product
from catalog.services import apply_confirmation

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


class ShopProductViewSet(viewsets.ModelViewSet):
    """A merchant's own catalogue, nested under the shop that owns it.

    Nested rather than flat because `Shop.owner` is a plain FK -- one user can
    own several shops, so "the" shop cannot be inferred from the request. The
    slug in the URL says which one.
    """

    permission_classes = [permissions.IsAuthenticated, IsOwnerOrReadOnly]
    serializer_class = ShopProductSerializer
    lookup_field = "slug"

    def get_shop(self):
        # Ownership is enforced once, here, for every action. Filtering on owner
        # rather than checking it afterwards makes someone else's shop a 404 and
        # not a 403, so the URL space does not confirm which slugs exist.
        return get_object_or_404(
            Shop, slug=self.kwargs["shop_slug"], owner=self.request.user
        )

    def get_queryset(self):
        return Product.objects.filter(shop=self.get_shop()).select_related(
            "category", "shop"
        )

    def perform_create(self, serializer):
        product = serializer.save(shop=self.get_shop())
        # A new listing opens fresh through the confirmation service, never by
        # setting the column here. `confirmation_status="fresh"` on its own is
        # both a no-op (it is the model default) and a lie: it leaves
        # `last_confirmed_at` NULL, which the sweep reads as stale and the
        # buyer-facing tier reports as "expired". `confirmed=` carries the
        # merchant's own availability answer rather than assuming in-stock.
        apply_confirmation(product, confirmed=product.is_available)
