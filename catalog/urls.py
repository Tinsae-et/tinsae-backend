from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import CategoryViewSet, ProductViewSet, PublicStatsView, SimilarProductsView

router = DefaultRouter()
router.register("categories", CategoryViewSet)
router.register("products", ProductViewSet, basename="product")

urlpatterns = router.urls + [
    path("similar/<slug:slug>/", SimilarProductsView.as_view(), name="similar"),
    path("public-stats/", PublicStatsView.as_view(), name="public-stats"),
]
