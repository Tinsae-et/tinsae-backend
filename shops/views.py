from rest_framework import permissions, viewsets

from .models import Shop
from .permissions import IsOwnerOrReadOnly
from .serializers import ShopListSerializer, ShopPublicSerializer, ShopSerializer


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
