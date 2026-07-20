from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path
from rest_framework_simplejwt import views as jwt_views

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/health/", include("health.urls")),
    path("api/v1/catalog/", include("catalog.urls")),
    path("api/v1/", include("shops.urls")),
    path(
        "api/v1/auth/token/",
        jwt_views.TokenObtainPairView.as_view(),
        name="token_obtain_pair",
    ),
    path(
        "api/v1/auth/token/refresh/",
        jwt_views.TokenRefreshView.as_view(),
        name="token_refresh",
    ),
] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
