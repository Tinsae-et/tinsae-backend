from rest_framework.routers import DefaultRouter

from .views import (
    CategoryViewSet,
    ProductfreshnessViewSet,
    ProductImageViewSet,
    ProductViewSet,
)

router = DefaultRouter()
router.register("categories", CategoryViewSet)
router.register("products", ProductViewSet, basename="product")
router.register("productsimage", ProductImageViewSet, basename="productsimage")
router.register(
    "productfreshness", ProductfreshnessViewSet, basename="productfreshness"
)

urlpatterns = router.urls
