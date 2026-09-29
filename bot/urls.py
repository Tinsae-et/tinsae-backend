from django.urls import path

from .api import (
    TelegramConnectView,
    TelegramDisconnectView,
    TelegramStatusView,
)

urlpatterns = [
    path(
        "shops/<slug:slug>/telegram/connect/",
        TelegramConnectView.as_view(),
        name="shop-telegram-connect",
    ),
    path(
        "shops/<slug:slug>/telegram/disconnect/",
        TelegramDisconnectView.as_view(),
        name="shop-telegram-disconnect",
    ),
    path(
        "shops/<slug:slug>/telegram/",
        TelegramStatusView.as_view(),
        name="shop-telegram-status",
    ),
]
