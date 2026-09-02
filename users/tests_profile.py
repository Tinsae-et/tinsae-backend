from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse_lazy
from rest_framework.test import APIClient

from .models import User

PASSWORD = "SecurePassword123!"


class ProfileTests(TestCase):
    url = reverse_lazy("me")

    def setUp(self):
        # The PATCH throttle counts in LocMemCache, which outlives a test.
        cache.clear()
        self.user = User.objects.create_user(
            username="hanna", email="hanna@example.com", password=PASSWORD
        )
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def test_get_returns_the_profile_shape(self):
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)
        for field in (
            "full_name",
            "preferred_area",
            "telegram_username",
            "phone_verified",
            "avatar",
            "date_joined",
            "shops",
        ):
            self.assertIn(field, resp.data)
        self.assertEqual(resp.data["shops"], [])
        self.assertFalse(resp.data["phone_verified"])

    def test_patch_updates_the_display_fields(self):
        resp = self.client.patch(
            self.url,
            {
                "full_name": "Hanna Alemu",
                "preferred_area": "Bole · Atlas",
                "telegram_username": "hanna",
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        self.user.refresh_from_db()
        self.assertEqual(self.user.full_name, "Hanna Alemu")
        self.assertEqual(self.user.preferred_area, "Bole · Atlas")
        self.assertEqual(self.user.telegram_username, "hanna")

    def test_accounts_predating_full_name_return_it_blank(self):
        """Not backfilled from the username, which is why it is blank here.

        `derive_username` produces things like `user2`; writing that into
        `full_name` would make a machine-made placeholder indistinguishable
        from a name the person chose. Clients fall back to `username`.
        """
        resp = self.client.get(self.url)
        self.assertEqual(resp.data["full_name"], "")
        self.assertEqual(resp.data["username"], "hanna")

    def test_email_cannot_be_changed_here(self):
        """A silent no-op, which is DRF's documented read-only behaviour.

        The point of the test is the account, not the status code: an
        unverified email change is an address takeover, and email is unique.
        """
        resp = self.client.patch(self.url, {"email": "someone@else.com"}, format="json")
        self.assertEqual(resp.status_code, 200)
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, "hanna@example.com")

    def test_phone_cannot_be_changed_here(self):
        self.user.phone = "+251911234567"
        self.user.save(update_fields=["phone"])

        self.client.patch(self.url, {"phone": "+251911111111"}, format="json")
        self.user.refresh_from_db()
        self.assertEqual(self.user.phone, "+251911234567")

    def test_full_name_drives_the_email_greeting(self):
        """`send_password_reset_email` greets by `get_short_name()`."""
        self.user.full_name = "Hanna Alemu"
        self.assertEqual(self.user.get_short_name(), "Hanna")
        self.assertEqual(self.user.get_full_name(), "Hanna Alemu")

    def test_anonymous_patch_is_rejected(self):
        resp = APIClient().patch(self.url, {"full_name": "x"}, format="json")
        self.assertEqual(resp.status_code, 401)
