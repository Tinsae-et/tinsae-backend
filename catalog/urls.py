from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import (
    CategorySchemaView,
    CategoryViewSet,
    ProductViewSet,
    PublicStatsView,
    SimilarProductsView,
)

router = DefaultRouter()
router.register("categories", CategoryViewSet)
router.register("products", ProductViewSet, basename="product")

urlpatterns = router.urls + [
    # Two segments, so the router's single-segment category detail route
    # (`categories/<pk>/`) cannot swallow it.
    path(
        "categories/<slug:slug>/schema/",
        CategorySchemaView.as_view(),
        name="category-schema",
    ),
    path("similar/<slug:slug>/", SimilarProductsView.as_view(), name="similar"),
    path("public-stats/", PublicStatsView.as_view(), name="public-stats"),
]
