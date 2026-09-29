from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework import status
from rest_framework.test import APIClient

from bot.models import ShopLinkCode
from bot.services import issue_link_code
from shops.models import Shop

User = get_user_model()


class TelegramApiTestCase(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            username="owner",
            email="owner@example.com",
            password="password123",
        )
        self.other_user = User.objects.create_user(
            username="other",
            email="other@example.com",
            password="password123",
        )
        self.shop = Shop.objects.create(
            name="Bole Electronics",
            slug="bole-electronics",
            owner=self.owner,
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.owner)

    def _url(self, action=""):
        if action:
            return f"/api/v1/shops/{self.shop.slug}/telegram/{action}/"
        return f"/api/v1/shops/{self.shop.slug}/telegram/"


class TelegramConnectApiTests(TelegramApiTestCase):
    @override_settings(TELEGRAM_BOT_USERNAME="tinsae_bot")
    def test_owner_gets_code_deep_link_bot_username_and_expires_at(self):
        response = self.client.post(self._url("connect"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.data
        self.assertTrue(data["code"].startswith("TIN-"))
        self.assertEqual(data["bot_username"], "tinsae_bot")
        self.assertEqual(
            data["deep_link"], f"https://t.me/tinsae_bot?start={data['code']}"
        )
        self.assertIsNotNone(data["expires_at"])

    @override_settings(TELEGRAM_BOT_USERNAME="@tinsae_bot")
    def test_leading_at_in_bot_username_is_stripped(self):
        response = self.client.post(self._url("connect"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["bot_username"], "tinsae_bot")
        self.assertEqual(
            response.data["deep_link"],
            f"https://t.me/tinsae_bot?start={response.data['code']}",
        )

    @override_settings(TELEGRAM_BOT_USERNAME="")
    def test_empty_bot_username_results_in_null_deep_link(self):
        response = self.client.post(self._url("connect"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIsNone(response.data["deep_link"])
        self.assertEqual(response.data["bot_username"], "")

    def test_non_owner_gets_404(self):
        self.client.force_authenticate(user=self.other_user)
        response = self.client.post(self._url("connect"))

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_unauthenticated_gets_401(self):
        self.client.logout()
        response = self.client.post(self._url("connect"))

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_issuing_again_retires_the_previous_code(self):
        res1 = self.client.post(self._url("connect"))
        self.assertEqual(res1.status_code, status.HTTP_200_OK)
        code1 = res1.data["code"]

        res2 = self.client.post(self._url("connect"))
        self.assertEqual(res2.status_code, status.HTTP_200_OK)
        code2 = res2.data["code"]

        self.assertNotEqual(code1, code2)

        obj1 = ShopLinkCode.objects.get(code=code1)
        obj2 = ShopLinkCode.objects.get(code=code2)

        self.assertTrue(obj1.is_expired)
        self.assertTrue(obj2.is_live)
        self.assertEqual(
            ShopLinkCode.objects.filter(
                shop=self.shop, used_at__isnull=True, expires_at__gt=timezone.now()
            ).count(),
            1,
        )


class TelegramStatusApiTests(TelegramApiTestCase):
    def test_idle_status_when_not_linked_and_not_blocked(self):
        response = self.client.get(self._url())

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["status"], "idle")
        self.assertIsNone(response.data["linked_at"])

    def test_connected_status_when_chat_id_set(self):
        self.shop.telegram_chat_id = "987654321"
        self.shop.save(update_fields=["telegram_chat_id"])

        response = self.client.get(self._url())

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["status"], "connected")
        self.assertIsNone(response.data["linked_at"])

    def test_blocked_status_when_blocked_at_set(self):
        self.shop.telegram_chat_id = "987654321"
        self.shop.blocked_at = timezone.now()
        self.shop.save(update_fields=["telegram_chat_id", "blocked_at"])

        response = self.client.get(self._url())

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["status"], "blocked")

    def test_linked_at_reflects_most_recent_used_code(self):
        t1 = timezone.now() - timedelta(days=2)
        code1 = issue_link_code(self.shop, created_by=self.owner)
        code1.used_at = t1
        code1.used_by_chat_id = "111"
        code1.save(update_fields=["used_at", "used_by_chat_id"])

        t2 = timezone.now() - timedelta(hours=1)
        code2 = issue_link_code(self.shop, created_by=self.owner)
        code2.used_at = t2
        code2.used_by_chat_id = "222"
        code2.save(update_fields=["used_at", "used_by_chat_id"])

        self.shop.telegram_chat_id = "222"
        self.shop.save(update_fields=["telegram_chat_id"])

        response = self.client.get(self._url())

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["status"], "connected")
        self.assertIsNotNone(response.data["linked_at"])
        # Verify it reflects the latest used code (t2, not t1)
        self.assertEqual(parse_datetime(response.data["linked_at"]), t2)

    def test_non_owner_gets_404(self):
        self.client.force_authenticate(user=self.other_user)
        response = self.client.get(self._url())

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_unauthenticated_gets_401(self):
        self.client.logout()
        response = self.client.get(self._url())

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)


class TelegramDisconnectApiTests(TelegramApiTestCase):
    def test_disconnect_clears_telegram_chat_id_and_blocked_at(self):
        self.shop.telegram_chat_id = "987654321"
        self.shop.blocked_at = timezone.now()
        self.shop.save(update_fields=["telegram_chat_id", "blocked_at"])

        code = issue_link_code(self.shop, created_by=self.owner)

        response = self.client.post(self._url("disconnect"))

        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.shop.refresh_from_db()
        self.assertEqual(self.shop.telegram_chat_id, "")
        self.assertIsNone(self.shop.blocked_at)

        # Code remains for audit
        self.assertTrue(ShopLinkCode.objects.filter(pk=code.pk).exists())

    def test_non_owner_gets_404(self):
        self.client.force_authenticate(user=self.other_user)
        response = self.client.post(self._url("disconnect"))

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_unauthenticated_gets_401(self):
        self.client.logout()
        response = self.client.post(self._url("disconnect"))

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
