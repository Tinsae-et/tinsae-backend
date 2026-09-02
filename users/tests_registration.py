from django.core.cache import cache
from django.urls import reverse_lazy
from rest_framework import status
from rest_framework.test import APITestCase

from shops.models import Shop
from users.models import User

PASSWORD = "SecurePassword123!"


class ThrottleResetMixin:
    def setUp(self):
        cache.clear()
        super().setUp()


class TestBuyerRegistration(ThrottleResetMixin, APITestCase):
    url = reverse_lazy("register")

    def test_buyer_registration_creates_no_shop(self):
        response = self.client.post(
            self.url,
            {"email": "alex@example.com", "password": PASSWORD, "role": "buyer"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["user"]["shops"], [])
        self.assertIn("access", response.data)
        self.assertIn("refresh", response.data)
        self.assertEqual(Shop.objects.count(), 0)

    def test_buyer_needs_no_phone(self):
        response = self.client.post(
            self.url,
            {"email": "nophone@example.com", "password": PASSWORD, "role": "buyer"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertIsNone(User.objects.get(email="nophone@example.com").phone)

    def test_username_is_derived_from_email(self):
        self.client.post(
            self.url,
            {"email": "alex@example.com", "password": PASSWORD, "role": "buyer"},
            format="json",
        )
        self.assertEqual(User.objects.get(email="alex@example.com").username, "alex")

    def test_derived_username_avoids_collision(self):
        User.objects.create_user(username="alex", email="taken@example.com")
        response = self.client.post(
            self.url,
            {"email": "alex@other.com", "password": PASSWORD, "role": "buyer"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["user"]["username"], "alex2")

    def test_email_is_required(self):
        response = self.client.post(
            self.url, {"password": PASSWORD, "role": "buyer"}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("email", response.data)

    def test_full_name_is_stored_as_typed(self):
        """Stored whole, never split into first/last -- see `User.get_full_name`."""
        response = self.client.post(
            self.url,
            {
                "email": "hanna@example.com",
                "password": PASSWORD,
                "role": "buyer",
                "full_name": "Hanna Alemu Bekele",
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        user = User.objects.get(email="hanna@example.com")
        self.assertEqual(user.full_name, "Hanna Alemu Bekele")
        self.assertEqual(user.first_name, "")
        self.assertEqual(user.last_name, "")
        self.assertEqual(response.data["user"]["full_name"], "Hanna Alemu Bekele")

    def test_full_name_is_optional_until_the_frontend_sends_it(self):
        response = self.client.post(
            self.url,
            {"email": "noname@example.com", "password": PASSWORD, "role": "buyer"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(User.objects.get(email="noname@example.com").full_name, "")

    def test_duplicate_email_is_rejected_case_insensitively(self):
        User.objects.create_user(username="sara", email="sara@example.com")
        response = self.client.post(
            self.url,
            {"email": "Sara@Example.com", "password": PASSWORD, "role": "buyer"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("email", response.data)


class TestOwnerRegistration(ThrottleResetMixin, APITestCase):
    url = reverse_lazy("register")

    payload = {
        "email": "sales@bolephones.et",
        "password": PASSWORD,
        "phone": "+251912345678",
        "role": "owner",
        "shop_name": "Bole Phones",
        "city": "Addis Ababa",
    }

    def test_owner_registration_creates_one_shop(self):
        response = self.client.post(self.url, self.payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(len(response.data["user"]["shops"]), 1)

        shop = Shop.objects.get()
        self.assertEqual(shop.name, "Bole Phones")
        self.assertEqual(shop.city, "Addis Ababa")
        self.assertEqual(shop.phone, "+251912345678")

    def test_explicit_owner_role_without_shop_name_is_rejected(self):
        """The trap this guards: a dropped field must not become a buyer."""
        payload = {**self.payload}
        del payload["shop_name"]

        response = self.client.post(self.url, payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("shop_name", response.data)
        self.assertEqual(User.objects.count(), 0)

    def test_owner_without_phone_is_rejected(self):
        payload = {**self.payload}
        del payload["phone"]

        response = self.client.post(self.url, payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("phone", response.data)

    def test_legacy_payload_without_role_still_creates_a_shop(self):
        """The frontend posts no `role` yet. Until it does, `shop_name` stands in."""
        payload = {**self.payload, "username": "bolephones"}
        del payload["role"]

        response = self.client.post(self.url, payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(len(response.data["user"]["shops"]), 1)
