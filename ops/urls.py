from django.urls import path

from .views import OpsHealthView, ops_health_page

urlpatterns = [path("health/", OpsHealthView.as_view(), name="ops-health")]

# Mounted outside the API prefix by config/urls.py -- it renders HTML and is
# session-gated rather than token-gated.
page_urlpatterns = [path("health/", ops_health_page, name="ops-health-page")]
