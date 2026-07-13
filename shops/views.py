from rest_framework import permissions, viewsets

from .models import Shop
from .permissions import IsOwnerOrReadOnly
from .serializers import ShopListSerializer, ShopSerializer


class ShopViewSet(viewsets.ModelViewSet):
    queryset = Shop.objects.all()
    lookup_field = "slug"
    permission_classes = [permissions.IsAuthenticatedOrReadOnly, IsOwnerOrReadOnly]

    def get_serializer_class(self):
        if self.action == "list":
            return ShopListSerializer
        return ShopSerializer

    def perform_create(self, serializer):
        serializer.save(owner=self.request.user)
