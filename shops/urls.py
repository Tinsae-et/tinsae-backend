from rest_framework.routers import DefaultRouter

from .views import ManageMyProductsViewSet, ShopViewSet

router = DefaultRouter()
router.register("shops", ShopViewSet)
router.register("products", ManageMyProductsViewSet)

urlpatterns = router.urls
