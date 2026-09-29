"""Tests for the weekly confirmation digest and digest-level confirmation."""

from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone
from django_celery_beat.models import PeriodicTask

from bot import digest
from bot.models import ConfirmationDigest, ConfirmationRequest, OutboundMessage
from bot.services import answer_confirmation_digest
from catalog.models import Category, ConfirmationStatus, Product
from shops.models import Shop

User = get_user_model()


class DigestBuildTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="u1", password="pw")
        self.shop = Shop.objects.create(
            name="Test Shop",
            owner=self.user,
            telegram_chat_id="12345",
        )
        self.category = Category.objects.create(name="Shoes", slug="shoes")

    def test_build_digest_lists_up_to_15_items(self):
        now = timezone.now()
        products = []
        for i in range(18):
            p = Product.objects.create(
                shop=self.shop,
                category=self.category,
                name=f"Shoe {i + 1}",
                brand="Nike",
                price=100.0 + i,
                currency="ETB",
                is_available=True,
                last_confirmed_at=now - timedelta(days=10),
            )
            products.append(p)

        due, text, markup = digest.build_digest(self.shop, digest_id=99)
        self.assertEqual(len(due), 18)
        self.assertIn("Weekly stock check for Test Shop:", text)
        self.assertIn("1. Nike Shoe 1 — 100.00 ETB", text)
        self.assertIn("15. Nike Shoe 15 — 114.00 ETB", text)
        self.assertNotIn("16. Nike Shoe 16", text)
        self.assertIn("…and 3 more", text)
        self.assertIn("Unanswered listings are hidden after 14 days.", text)

        # Check markup
        rows = markup.inline_keyboard
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0].text, "✅ All still available")
        self.assertEqual(rows[0][0].callback_data, "da:99")


class DigestWorkflowTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="u2", password="pw")
        self.shop = Shop.objects.create(
            name="Alpha Boutique",
            owner=self.user,
            telegram_chat_id="999888",
        )
        self.category = Category.objects.create(name="Clothes", slug="clothes")
        self.now = timezone.now()

    def _create_due_product(self, name="Shirt", days_ago=10):
        return Product.objects.create(
            shop=self.shop,
            category=self.category,
            name=name,
            brand="Zara",
            price=250.0,
            currency="ETB",
            is_available=True,
            last_confirmed_at=self.now - timedelta(days=days_ago),
        )

    def test_send_due_digests_creates_digest_requests_and_outbound(self):
        p1 = self._create_due_product("Shirt 1", days_ago=10)
        p2 = self._create_due_product("Shirt 2", days_ago=12)

        sent_count = digest.send_due_digests()
        self.assertEqual(sent_count, 1)

        today = timezone.localtime(timezone.now()).date()
        digest_obj = ConfirmationDigest.objects.get(shop=self.shop, for_date=today)
        self.assertIsNotNone(digest_obj)

        # ConfirmationRequests created and linked
        requests = list(digest_obj.requests.all())
        self.assertEqual(len(requests), 2)
        for req in requests:
            self.assertEqual(req.status, "pending")
            self.assertEqual(req.shop, self.shop)
            self.assertIn(req.product, [p1, p2])

        # OutboundMessage created
        outbound = OutboundMessage.objects.get(chat_id="999888", kind="digest")
        self.assertEqual(outbound.dedupe_key, f"digest:{self.shop.pk}:{today}")
        self.assertIn("Zara Shirt 1", outbound.payload["text"])
        self.assertIn("reply_markup", outbound.payload)
        self.assertEqual(outbound.payload["digest_id"], digest_obj.pk)

    def test_cadence_gate_running_twice_in_one_day_is_noop(self):
        self._create_due_product("Shirt", days_ago=10)
        sent_first = digest.send_due_digests()
        self.assertEqual(sent_first, 1)

        sent_second = digest.send_due_digests()
        self.assertEqual(sent_second, 0)
        self.assertEqual(ConfirmationDigest.objects.filter(shop=self.shop).count(), 1)
        self.assertEqual(OutboundMessage.objects.filter(chat_id="999888").count(), 1)

    def test_cadence_gate_shop_with_recent_digest_is_skipped(self):
        today = timezone.localtime(timezone.now()).date()
        # Last digest was 5 days ago (< CONFIRMATION_INTERVAL_DAYS=7)
        ConfirmationDigest.objects.create(
            shop=self.shop,
            for_date=today - timedelta(days=5),
        )
        self._create_due_product("Shirt", days_ago=10)

        sent = digest.send_due_digests()
        self.assertEqual(sent, 0)
        self.assertEqual(ConfirmationDigest.objects.filter(shop=self.shop).count(), 1)

    def test_cadence_gate_shop_with_old_digest_gets_new_digest(self):
        today = timezone.localtime(timezone.now()).date()
        # Last digest was 8 days ago (>= CONFIRMATION_INTERVAL_DAYS=7)
        ConfirmationDigest.objects.create(
            shop=self.shop,
            for_date=today - timedelta(days=8),
        )
        self._create_due_product("Shirt", days_ago=10)

        sent = digest.send_due_digests()
        self.assertEqual(sent, 1)
        self.assertEqual(ConfirmationDigest.objects.filter(shop=self.shop).count(), 2)

    def test_unlinked_shop_is_skipped(self):
        self.shop.telegram_chat_id = ""
        self.shop.save(update_fields=["telegram_chat_id"])
        self._create_due_product("Shirt", days_ago=10)

        sent = digest.send_due_digests()
        self.assertEqual(sent, 0)
        self.assertEqual(ConfirmationDigest.objects.count(), 0)

    def test_blocked_shop_is_skipped(self):
        self.shop.blocked_at = timezone.now()
        self.shop.save(update_fields=["blocked_at"])
        self._create_due_product("Shirt", days_ago=10)

        sent = digest.send_due_digests()
        self.assertEqual(sent, 0)
        self.assertEqual(ConfirmationDigest.objects.count(), 0)

    def test_shop_with_no_due_products_is_skipped(self):
        # Fresh product (last confirmed 2 days ago)
        self._create_due_product("Shirt", days_ago=2)

        sent = digest.send_due_digests()
        self.assertEqual(sent, 0)
        self.assertEqual(ConfirmationDigest.objects.count(), 0)


class AnswerConfirmationDigestTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="u3", password="pw")
        self.shop = Shop.objects.create(
            name="Mega Store",
            owner=self.user,
            telegram_chat_id="777111",
        )
        self.category = Category.objects.create(name="Gadgets", slug="gadgets")
        self.now = timezone.now()
        self.p1 = Product.objects.create(
            shop=self.shop,
            category=self.category,
            name="Gadget 1",
            brand="BrandA",
            price=150.0,
            currency="ETB",
            is_available=True,
            last_confirmed_at=self.now - timedelta(days=10),
        )
        self.p2 = Product.objects.create(
            shop=self.shop,
            category=self.category,
            name="Gadget 2",
            brand="BrandB",
            price=250.0,
            currency="ETB",
            is_available=True,
            last_confirmed_at=self.now - timedelta(days=10),
        )

        today = timezone.localtime(timezone.now()).date()
        self.digest = ConfirmationDigest.objects.create(shop=self.shop, for_date=today)
        self.req1 = ConfirmationRequest.objects.create(
            product=self.p1, shop=self.shop, digest=self.digest, status="pending"
        )
        self.req2 = ConfirmationRequest.objects.create(
            product=self.p2, shop=self.shop, digest=self.digest, status="pending"
        )

    def test_answer_all_still_available_marks_all_confirmed(self):
        outcome, count = answer_confirmation_digest(
            "777111", self.digest.pk, available=True
        )
        self.assertEqual(outcome, "confirmed")
        self.assertEqual(count, 2)

        self.req1.refresh_from_db()
        self.req2.refresh_from_db()
        self.assertEqual(self.req1.status, "confirmed")
        self.assertEqual(self.req2.status, "confirmed")

        self.p1.refresh_from_db()
        self.p2.refresh_from_db()
        self.assertEqual(self.p1.confirmation_status, ConfirmationStatus.FRESH)
        self.assertEqual(self.p2.confirmation_status, ConfirmationStatus.FRESH)
        self.assertTrue(self.p1.is_available)
        self.assertTrue(self.p2.is_available)
        self.assertGreaterEqual(self.p1.last_confirmed_at, self.now)

        self.digest.refresh_from_db()
        self.assertIsNotNone(self.digest.closed_at)

    def test_answer_digest_second_time_returns_already_answered(self):
        outcome, count = answer_confirmation_digest(
            "777111", self.digest.pk, available=True
        )
        self.assertEqual(outcome, "confirmed")
        self.assertEqual(count, 2)

        # Second attempt
        outcome2, count2 = answer_confirmation_digest(
            "777111", self.digest.pk, available=True
        )
        self.assertEqual(outcome2, "already_answered")
        self.assertEqual(count2, 0)

    def test_answer_digest_from_foreign_chat_returns_not_found(self):
        outcome, count = answer_confirmation_digest(
            "999999", self.digest.pk, available=True
        )
        self.assertEqual(outcome, "not_found")
        self.assertEqual(count, 0)

        self.req1.refresh_from_db()
        self.assertEqual(self.req1.status, "pending")
        self.digest.refresh_from_db()
        self.assertIsNone(self.digest.closed_at)

    def test_answer_digest_nonexistent_returns_not_found(self):
        outcome, count = answer_confirmation_digest("777111", 999999, available=True)
        self.assertEqual(outcome, "not_found")
        self.assertEqual(count, 0)


class SetupBotSchedulesTests(TestCase):
    def test_setup_bot_schedules_creates_crontab_and_periodic_tasks(self):
        call_command("setup_bot_schedules")

        drain_task = PeriodicTask.objects.get(name="bot-drain-outbox")
        self.assertEqual(drain_task.task, "bot.tasks.drain_outbox")
        self.assertTrue(drain_task.enabled)

        digest_task = PeriodicTask.objects.get(name="bot-send-due-digests")
        self.assertEqual(digest_task.task, "bot.tasks.send_due_digests")
        self.assertTrue(digest_task.enabled)
        self.assertIsNotNone(digest_task.crontab)
        self.assertEqual(digest_task.crontab.hour, "8")
        self.assertEqual(digest_task.crontab.minute, "0")
        self.assertEqual(str(digest_task.crontab.timezone), settings.TIME_ZONE)
