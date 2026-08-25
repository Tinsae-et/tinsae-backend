from django.urls import path

from . import views

urlpatterns = [
    path("register/", views.ShopOwnerRegistrationView.as_view(), name="register"),
    path("logout/", views.LogoutView.as_view(), name="logout"),
    path(
        "password/change/",
        views.PasswordChangeView.as_view(),
        name="password_change",
    ),
    path(
        "password/reset/",
        views.PasswordResetRequestView.as_view(),
        name="password_reset",
    ),
    path(
        "password/reset/confirm/",
        views.PasswordResetConfirmView.as_view(),
        name="password_reset_confirm",
    ),
    path("me/", views.GetMyShops.as_view(), name="me"),
]
