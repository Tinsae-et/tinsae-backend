from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import ShopProductViewSet, ShopViewSet

router = DefaultRouter()
router.register("shops", ShopViewSet)

# Wired by hand rather than with a nested-router dependency: two routes do not
# justify one. The router's own shop detail pattern matches a single path
# segment, so `shops/<slug>/products/` cannot collide with it.
shop_products_list = ShopProductViewSet.as_view({"get": "list", "post": "create"})
shop_products_detail = ShopProductViewSet.as_view(
    {
        "get": "retrieve",
        "put": "update",
        "patch": "partial_update",
        "delete": "destroy",
    }
)

urlpatterns = router.urls + [
    path(
        "shops/<slug:shop_slug>/products/",
        shop_products_list,
        name="shop-products-list",
    ),
    path(
        "shops/<slug:shop_slug>/products/<slug:slug>/",
        shop_products_detail,
        name="shop-products-detail",
    ),
]
