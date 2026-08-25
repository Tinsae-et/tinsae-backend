from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

from ops.urls import page_urlpatterns as ops_page_urlpatterns
from users.views import ThrottledTokenObtainPairView, ThrottledTokenRefreshView

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/health/", include("health.urls")),
    path("api/v1/catalog/", include("catalog.urls")),
    path("api/v1/", include("shops.urls")),
    path("api/v1/ops/", include("ops.urls")),
    # The HTML health page. Kept off the /admin/ prefix on purpose: that path is
    # consumed whole by admin.site.urls, which 404s unknown sub-paths rather
    # than falling through to later patterns.
    path("ops/", include(ops_page_urlpatterns)),
    path("api/v1/auth/", include("users.urls")),
    path(
        "api/v1/auth/token/",
        ThrottledTokenObtainPairView.as_view(),
        name="token_obtain_pair",
    ),
    path(
        "api/v1/auth/token/refresh/",
        ThrottledTokenRefreshView.as_view(),
        name="token_refresh",
    ),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
