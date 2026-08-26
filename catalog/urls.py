from rest_framework.routers import DefaultRouter

from .views import CategoryViewSet, ProductSearchViewSet, ProductViewSet

router = DefaultRouter()
router.register("categories", CategoryViewSet)
router.register("products", ProductViewSet, basename="product")
router.register("productsearch", ProductSearchViewSet, basename="productsearch")

urlpatterns = router.urls
