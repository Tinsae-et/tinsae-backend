from django.urls import path

from . import views

urlpatterns = [
    path("register/", views.ShopOwnerRegistrationView.as_view(), name="register"),
    path("me/", views.GetMyShops.as_view(), name="me"),
]
