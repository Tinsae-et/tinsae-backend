from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import (
    PublicShopProductsView,
    ShopProductImageViewSet,
    ShopProductViewSet,
    ShopViewSet,
    VerificationViewSet,
)

router = DefaultRouter()
router.register("shops", ShopViewSet)

# Wired by hand rather than with a nested-router dependency. The cost of that
# choice is here: @action does not self-route, so every action needs a line
# below. Worth revisiting if this list keeps growing.
#
# The router's own shop-detail pattern matches a single path segment, so nothing
# under `shops/<slug>/...` can collide with it.
PRODUCTS = "shops/<slug:shop_slug>/products/"
PRODUCT = "shops/<slug:shop_slug>/products/<slug:slug>/"
IMAGES = "shops/<slug:shop_slug>/products/<slug:product_slug>/images/"
VERIFICATION = "shops/<slug:shop_slug>/verification/"


verification_list = VerificationViewSet.as_view({"get": "list", "post": "create"})
verification_detail = VerificationViewSet.as_view({"get": "retrieve"})
products_list = ShopProductViewSet.as_view({"get": "list", "post": "create"})
products_detail = ShopProductViewSet.as_view(
    {
        "get": "retrieve",
        "put": "update",
        "patch": "partial_update",
        "delete": "destroy",
    }
)
confirm_all = ShopProductViewSet.as_view({"post": "confirm_all"})
confirm = ShopProductViewSet.as_view({"post": "confirm"})
mark_sold = ShopProductViewSet.as_view({"post": "mark_sold"})
pause = ShopProductViewSet.as_view({"post": "pause"})
resume = ShopProductViewSet.as_view({"post": "resume"})

images_list = ShopProductImageViewSet.as_view({"get": "list", "post": "create"})
images_detail = ShopProductImageViewSet.as_view(
    {
        "get": "retrieve",
        "put": "update",
        "patch": "partial_update",
        "delete": "destroy",
    }
)

urlpatterns = router.urls + [
    # `confirm-all` is declared before the detail route so it is not swallowed
    # as a product slug.
    path(PRODUCTS + "confirm-all/", confirm_all, name="shop-products-confirm-all"),
    path(PRODUCTS, products_list, name="shop-products-list"),
    path(PRODUCT + "confirm/", confirm, name="shop-products-confirm"),
    path(PRODUCT + "mark-sold/", mark_sold, name="shop-products-mark-sold"),
    path(PRODUCT + "pause/", pause, name="shop-products-pause"),
    path(PRODUCT + "resume/", resume, name="shop-products-resume"),
    path(IMAGES, images_list, name="shop-product-images-list"),
    path(IMAGES + "<int:pk>/", images_detail, name="shop-product-images-detail"),
    path(PRODUCT, products_detail, name="shop-products-detail"),
    path(VERIFICATION, verification_list, name="shop-verification-list"),
    path(VERIFICATION + "state/", verification_detail, name="shop-verification-detail"),
    path(
        "shop/<slug:slug>/products/",
        PublicShopProductsView.as_view(),
        name="public-shop-products-list",
    ),
]
