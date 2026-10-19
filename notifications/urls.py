"""URL configuration for the notifications API."""

from django.urls import path

from .views import (
    NotificationListView,
    NotificationMarkAllReadView,
    NotificationMarkReadView,
    NotificationUnreadCountView,
)

app_name = "notifications"

urlpatterns = [
    path("", NotificationListView.as_view(), name="list"),
    path(
        "unread-count/",
        NotificationUnreadCountView.as_view(),
        name="unread_count",
    ),
    path(
        "<int:pk>/read/",
        NotificationMarkReadView.as_view(),
        name="mark_read",
    ),
    path(
        "read-all/",
        NotificationMarkAllReadView.as_view(),
        name="mark_all_read",
    ),
]
