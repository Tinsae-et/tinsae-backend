from django.urls import include, path
from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter()
router.register("buyer", views.BuyerInquiryViewSet, basename="buyer")
router.register("shop", views.ShopInquiryViewSet, basename="shop")

urlpatterns = [path("", include(router.urls))]
