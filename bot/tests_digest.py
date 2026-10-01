import io
from datetime import timedelta
from unittest import mock

from asgiref.sync import async_to_sync
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone
from django_celery_beat.models import PeriodicTask

from bot import digest, messages
from bot.management.commands.run_bot import on_callback, on_text
from bot.models import ConfirmationDigest, ConfirmationRequest, OutboundMessage
from bot.parsing import (
    format_numeric_selection_callback,
    parse_digest_all,
    parse_numeric_selection_callback,
    parse_sold_indices,
)
from bot.selectors import resolve_digest_for_chat
from bot.services import (
    answer_confirmation_digest,
    answer_digest_selection,
    handle_digest_text,
    send_confirmation_prompt,
    send_reminders,
    sweep,
)
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
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0][0].text, "✅ All still available")
        self.assertEqual(rows[0][0].callback_data, "da:99")
        self.assertEqual(rows[1][0].text, "✏️ Check one by one")
        self.assertEqual(rows[1][0].callback_data, "drev:99")


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

        tasks = {t.name: t.task for t in PeriodicTask.objects.all()}
        self.assertIn("bot-drain-outbox", tasks)
        self.assertEqual(tasks["bot-drain-outbox"], "bot.tasks.drain_outbox")

        self.assertIn("bot-send-due-digests", tasks)
        self.assertEqual(tasks["bot-send-due-digests"], "bot.tasks.send_due_digests")

        self.assertIn("bot-sweep", tasks)
        self.assertEqual(tasks["bot-sweep"], "bot.tasks.sweep")

        self.assertIn("bot-send-reminders", tasks)
        self.assertEqual(tasks["bot-send-reminders"], "bot.tasks.send_reminders")

        self.assertIn("bot-prune", tasks)
        self.assertEqual(tasks["bot-prune"], "bot.tasks.prune")

        digest_task = PeriodicTask.objects.get(name="bot-send-due-digests")
        self.assertTrue(digest_task.enabled)
        self.assertIsNotNone(digest_task.crontab)
        # A morning window, so a beat restart at 08:00 catches up instead of
        # losing the day's digest.
        self.assertEqual(digest_task.crontab.hour, "8,9,10,11")
        self.assertEqual(digest_task.crontab.minute, "0")
        self.assertEqual(str(digest_task.crontab.timezone), settings.TIME_ZONE)

        reminder_task = PeriodicTask.objects.get(name="bot-send-reminders")
        self.assertEqual(reminder_task.crontab.hour, "13,14,15,16")

        # Second call is idempotent (running twice does not duplicate)
        call_command("setup_bot_schedules")
        self.assertEqual(
            PeriodicTask.objects.filter(name__startswith="bot-").count(), 5
        )


class DigestParsingTests(SimpleTestCase):
    def test_parse_digest_all(self):
        for affirmative in (
            "all",
            "everything",
            "all available",
            "all of them",
            "all still available",
            "ሁሉም",
            "ሁሉም አሉ",
            "yes",
            "y",
            "ok",
            "sure",
            "available",
            "ALL",
            "All Available!",
            "ሁሉም!",
        ):
            self.assertTrue(
                parse_digest_all(affirmative), f"Expected True for {affirmative!r}"
            )

        for negative_or_unparsed in (
            "no",
            "sold",
            "sold out",
            "random",
            "",
            "2 5",
            "hello",
        ):
            self.assertFalse(
                parse_digest_all(negative_or_unparsed),
                f"Expected False for {negative_or_unparsed!r}",
            )

    def test_parse_sold_indices(self):
        sold_cases = [
            ("sold 2 5", [2, 5]),
            ("sold 2, 5", [2, 5]),
            ("2 5 sold", [2, 5]),
            ("sold: 2, 5", [2, 5]),
            ("2,5 sold out", [2, 5]),
            ("2 and 5 sold", [2, 5]),
            ("2 5 የለም", [2, 5]),
            ("sold 2 5 2", [2, 5]),
            ("sold 5 2", [5, 2]),
            ("sold 2 99", [2]),
            ("sold out 3", [3]),
        ]
        for text, expected_indices in sold_cases:
            kind, indices = parse_sold_indices(text)
            self.assertEqual(kind, "sold", f"Failed kind for {text!r}")
            self.assertEqual(indices, expected_indices, f"Failed indices for {text!r}")

        numbers_cases = [
            ("2 5", [2, 5]),
            ("2, 5", [2, 5]),
            ("2,5", [2, 5]),
            ("2", [2]),
        ]
        for text, expected_indices in numbers_cases:
            kind, indices = parse_sold_indices(text)
            self.assertEqual(kind, "numbers", f"Failed kind for {text!r}")
            self.assertEqual(indices, expected_indices, f"Failed indices for {text!r}")

        unparsed_cases = [
            "sold 99",
            "sold 0",
            "hello 2 5",
            "",
            "random text",
        ]
        for text in unparsed_cases:
            kind, indices = parse_sold_indices(text)
            self.assertIsNone(kind, f"Expected None kind for {text!r}")
            self.assertEqual(indices, [], f"Expected empty indices for {text!r}")

    def test_numeric_selection_callback(self):
        cb_avail = format_numeric_selection_callback(42, "a", [1, 3])
        self.assertEqual(cb_avail, "ni:42:a:1.3")
        self.assertEqual(parse_numeric_selection_callback(cb_avail), (42, "a", [1, 3]))

        cb_sold = format_numeric_selection_callback(42, "s", [2, 5])
        self.assertEqual(cb_sold, "ni:42:s:2.5")
        self.assertEqual(parse_numeric_selection_callback(cb_sold), (42, "s", [2, 5]))

        for malformed in ("ni:42:x:1.3", "ni:abc:a:1.3", "ni:42:a:", "invalid", ""):
            self.assertIsNone(parse_numeric_selection_callback(malformed))

    def test_numeric_selection_callback_worst_case_byte_length(self):
        indices_15 = list(range(1, 16))
        cb = format_numeric_selection_callback(999999, "s", indices_15)
        self.assertLessEqual(len(cb.encode("utf-8")), 64)


class DigestTargetResolutionTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="u_res", password="pw")
        self.shop1 = Shop.objects.create(
            name="Shop 1", owner=self.user, telegram_chat_id="999888"
        )
        self.today = timezone.localtime(timezone.now()).date()

    def test_single_open_digest_resolves(self):
        digest = ConfirmationDigest.objects.create(shop=self.shop1, for_date=self.today)
        resolved = resolve_digest_for_chat("999888")
        self.assertEqual(resolved, digest)

    def test_zero_open_digests_returns_none(self):
        ConfirmationDigest.objects.create(
            shop=self.shop1, for_date=self.today, closed_at=timezone.now()
        )
        self.assertIsNone(resolve_digest_for_chat("999888"))

    def test_multiple_open_digests_without_reply_to_returns_none(self):
        shop2 = Shop.objects.create(
            name="Shop 2", owner=self.user, telegram_chat_id="999888"
        )
        d1 = ConfirmationDigest.objects.create(
            shop=self.shop1,
            for_date=self.today - timedelta(days=7),
            telegram_message_id="111",
        )
        d2 = ConfirmationDigest.objects.create(
            shop=shop2, for_date=self.today, telegram_message_id="222"
        )
        # Without reply_to -> None
        self.assertIsNone(resolve_digest_for_chat("999888"))

        # With reply_to -> precise match
        self.assertEqual(resolve_digest_for_chat("999888", reply_to_message_id=222), d2)
        self.assertEqual(resolve_digest_for_chat("999888", reply_to_message_id=111), d1)

    def test_reply_to_matches_closed_digest(self):
        digest = ConfirmationDigest.objects.create(
            shop=self.shop1,
            for_date=self.today,
            telegram_message_id="333",
            closed_at=timezone.now(),
        )
        self.assertEqual(
            resolve_digest_for_chat("999888", reply_to_message_id=333), digest
        )

    def test_foreign_or_empty_chat_returns_none(self):
        ConfirmationDigest.objects.create(shop=self.shop1, for_date=self.today)
        self.assertIsNone(resolve_digest_for_chat("777777"))
        self.assertIsNone(resolve_digest_for_chat(""))
        self.assertIsNone(resolve_digest_for_chat(None))


class DigestTextReplyTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="u_txt", password="pw")
        self.shop = Shop.objects.create(
            name="Text Shop", owner=self.user, telegram_chat_id="999888"
        )
        self.category = Category.objects.create(name="Items", slug="items")
        self.now = timezone.now()
        self.p1 = Product.objects.create(
            shop=self.shop,
            category=self.category,
            name="Prod 1",
            brand="BrandA",
            price=10.0,
            currency="ETB",
            is_available=True,
            last_confirmed_at=self.now - timedelta(days=10),
        )
        self.p2 = Product.objects.create(
            shop=self.shop,
            category=self.category,
            name="Prod 2",
            brand="BrandB",
            price=20.0,
            currency="ETB",
            is_available=True,
            last_confirmed_at=self.now - timedelta(days=10),
        )
        self.p3 = Product.objects.create(
            shop=self.shop,
            category=self.category,
            name="Prod 3",
            brand="BrandC",
            price=30.0,
            currency="ETB",
            is_available=True,
            last_confirmed_at=self.now - timedelta(days=10),
        )
        today = timezone.localtime(timezone.now()).date()
        self.digest = ConfirmationDigest.objects.create(shop=self.shop, for_date=today)
        self.r1 = ConfirmationRequest.objects.create(
            product=self.p1, shop=self.shop, digest=self.digest, status="pending"
        )
        self.r2 = ConfirmationRequest.objects.create(
            product=self.p2, shop=self.shop, digest=self.digest, status="pending"
        )
        self.r3 = ConfirmationRequest.objects.create(
            product=self.p3, shop=self.shop, digest=self.digest, status="pending"
        )

    def test_all_confirms_open_digest(self):
        outcome = handle_digest_text("999888", "all")
        self.assertEqual(outcome, ("all_available", self.digest.pk, 3))

        self.r1.refresh_from_db()
        self.r2.refresh_from_db()
        self.r3.refresh_from_db()
        self.assertEqual(self.r1.status, "confirmed")
        self.assertEqual(self.r2.status, "confirmed")
        self.assertEqual(self.r3.status, "confirmed")

        self.digest.refresh_from_db()
        self.assertIsNotNone(self.digest.closed_at)

    def test_sold_indices_marks_sold_and_reports_remaining(self):
        outcome = handle_digest_text("999888", "sold 1 3")
        self.assertEqual(outcome[0], "sold")
        self.assertEqual(outcome[1], ["BrandA Prod 1", "BrandC Prod 3"])
        self.assertEqual(outcome[2], 1)

        self.r1.refresh_from_db()
        self.r2.refresh_from_db()
        self.r3.refresh_from_db()
        self.assertEqual(self.r1.status, "sold_out")
        self.assertEqual(self.r2.status, "pending")
        self.assertEqual(self.r3.status, "sold_out")

        self.digest.refresh_from_db()
        self.assertIsNone(self.digest.closed_at)

        # Mark remaining item sold
        outcome2 = handle_digest_text("999888", "sold 2")
        self.assertEqual(outcome2[0], "sold")
        self.assertEqual(outcome2[1], ["BrandB Prod 2"])
        self.assertEqual(outcome2[2], 0)

        self.digest.refresh_from_db()
        self.assertIsNotNone(self.digest.closed_at)

    def test_numeric_only_is_ambiguous_without_mutating(self):
        outcome = handle_digest_text("999888", "1 2")
        self.assertEqual(outcome, ("ambiguous", self.digest.pk, [1, 2]))

        self.r1.refresh_from_db()
        self.r2.refresh_from_db()
        self.assertEqual(self.r1.status, "pending")
        self.assertEqual(self.r2.status, "pending")
        self.digest.refresh_from_db()
        self.assertIsNone(self.digest.closed_at)

    def test_answer_digest_selection_confirms_subset(self):
        res = answer_digest_selection(
            "999888", self.digest.pk, available=True, indices=[1, 2]
        )
        self.assertEqual(res, ("confirmed", 2))
        self.assertEqual(res.labels, ["BrandA Prod 1", "BrandB Prod 2"])
        self.assertEqual(res.remaining, 1)

        self.r1.refresh_from_db()
        self.r2.refresh_from_db()
        self.r3.refresh_from_db()
        self.assertEqual(self.r1.status, "confirmed")
        self.assertEqual(self.r2.status, "confirmed")
        self.assertEqual(self.r3.status, "pending")
        self.digest.refresh_from_db()
        self.assertIsNone(self.digest.closed_at)

    def test_answer_digest_selection_closes_when_all_answered(self):
        answer_digest_selection(
            "999888", self.digest.pk, available=True, indices=[1, 2]
        )
        res2 = answer_digest_selection(
            "999888", self.digest.pk, available=False, indices=[3]
        )
        self.assertEqual(res2, ("sold_out", 1))
        self.assertEqual(res2.remaining, 0)

        self.digest.refresh_from_db()
        self.assertIsNotNone(self.digest.closed_at)

    def test_out_of_range_indices(self):
        outcome1 = handle_digest_text("999888", "sold 99")
        self.assertEqual(outcome1, ("unparsed", self.digest.pk))

        outcome2 = handle_digest_text("999888", "sold 10")
        self.assertEqual(outcome2, ("unparsed", self.digest.pk))

        # Mixed valid and out-of-range: marks valid, ignores out-of-range
        outcome3 = handle_digest_text("999888", "sold 1 10")
        self.assertEqual(outcome3[0], "sold")
        self.assertEqual(outcome3[1], ["BrandA Prod 1"])
        self.assertEqual(outcome3[2], 2)

    def test_closed_digest_handling(self):
        self.digest.closed_at = timezone.now()
        self.digest.telegram_message_id = "999"
        self.digest.save()

        # Reply targeting closed digest with answer attempt -> already_answered
        outcome = handle_digest_text("999888", "all", reply_to_message_id=999)
        self.assertEqual(outcome, ("already_answered", self.digest.pk))

        outcome = handle_digest_text("999888", "sold 1", reply_to_message_id=999)
        self.assertEqual(outcome, ("already_answered", self.digest.pk))

        outcome = handle_digest_text("999888", "1 2", reply_to_message_id=999)
        self.assertEqual(outcome, ("already_answered", self.digest.pk))

        # Non-answer attempt against closed digest -> None (falls through)
        outcome = handle_digest_text("999888", "hello", reply_to_message_id=999)
        self.assertIsNone(outcome)

    def test_foreign_chat_cannot_answer(self):
        self.assertIsNone(handle_digest_text("777777", "all"))
        res = answer_digest_selection(
            "777777", self.digest.pk, available=True, indices=[1]
        )
        self.assertEqual(res, ("not_found", 0))


class DigestRunBotTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="u_bot", password="pw")
        self.shop = Shop.objects.create(
            name="Bot Shop",
            owner=self.user,
            telegram_chat_id="888999",
        )
        self.category = Category.objects.create(name="Items", slug="items")
        self.p1 = Product.objects.create(
            shop=self.shop,
            category=self.category,
            name="Prod 1",
            brand="BrandA",
            price=10.0,
            currency="ETB",
            is_available=True,
        )
        self.p2 = Product.objects.create(
            shop=self.shop,
            category=self.category,
            name="Prod 2",
            brand="BrandB",
            price=20.0,
            currency="ETB",
            is_available=True,
        )
        today = timezone.localtime(timezone.now()).date()
        self.digest = ConfirmationDigest.objects.create(shop=self.shop, for_date=today)
        self.r1 = ConfirmationRequest.objects.create(
            product=self.p1, shop=self.shop, digest=self.digest, status="pending"
        )
        self.r2 = ConfirmationRequest.objects.create(
            product=self.p2, shop=self.shop, digest=self.digest, status="pending"
        )

    def _message_update(self, text, chat_id="888999", reply_to_message_id=None):
        update = mock.Mock()
        update.message = mock.AsyncMock()
        update.message.text = text
        update.message.reply_to_message = (
            mock.Mock(message_id=reply_to_message_id)
            if reply_to_message_id is not None
            else None
        )
        update.effective_chat.id = int(chat_id)
        return update, update.message

    def _callback_update(self, data, chat_id="888999"):
        update = mock.Mock()
        query = mock.Mock()
        query.data = data
        query.message = mock.AsyncMock()
        query.message.chat.id = int(chat_id)
        query.answer = mock.AsyncMock()
        query.edit_message_text = mock.AsyncMock()
        update.callback_query = query
        update.effective_chat.id = int(chat_id)
        return update, query

    def test_on_text_all_confirms_open_digest(self):
        update, message = self._message_update("all")
        context = mock.Mock()
        async_to_sync(on_text)(update, context)

        message.reply_text.assert_called_once_with(messages.digest_ack(2))
        self.digest.refresh_from_db()
        self.assertIsNotNone(self.digest.closed_at)

    def test_on_text_sold_marks_items_sold(self):
        update, message = self._message_update("sold 1")
        context = mock.Mock()
        async_to_sync(on_text)(update, context)

        self.r1.refresh_from_db()
        self.assertEqual(self.r1.status, "sold_out")
        message.reply_text.assert_called_once_with(
            messages.digest_sold_ack(["BrandA Prod 1"], 1)
        )

    def test_on_text_numbers_prompts_ambiguous(self):
        update, message = self._message_update("1 2")
        context = mock.Mock()
        async_to_sync(on_text)(update, context)

        self.r1.refresh_from_db()
        self.assertEqual(self.r1.status, "pending")
        message.reply_text.assert_called_once()
        args, kwargs = message.reply_text.call_args
        self.assertEqual(args[0], messages.digest_ambiguous([1, 2]))
        self.assertIn("reply_markup", kwargs)

    def test_on_text_unparsed_shows_digest_markup(self):
        update, message = self._message_update("sold 99")
        context = mock.Mock()
        async_to_sync(on_text)(update, context)

        message.reply_text.assert_called_once()
        args, kwargs = message.reply_text.call_args
        self.assertEqual(args[0], messages.digest_unparsed())
        self.assertIn("reply_markup", kwargs)

    def test_on_callback_numeric_selection_available(self):
        cb_data = format_numeric_selection_callback(self.digest.pk, "a", [1, 2])
        update, query = self._callback_update(cb_data)
        context = mock.Mock()
        async_to_sync(on_callback)(update, context)

        query.answer.assert_called_once_with(messages.response_recorded())
        query.edit_message_text.assert_called_once_with(messages.digest_ack(2))
        self.r1.refresh_from_db()
        self.r2.refresh_from_db()
        self.assertEqual(self.r1.status, "confirmed")
        self.assertEqual(self.r2.status, "confirmed")
        self.digest.refresh_from_db()
        self.assertIsNotNone(self.digest.closed_at)

    def test_on_callback_numeric_selection_sold(self):
        cb_data = format_numeric_selection_callback(self.digest.pk, "s", [1])
        update, query = self._callback_update(cb_data)
        context = mock.Mock()
        async_to_sync(on_callback)(update, context)

        query.answer.assert_called_once_with(messages.response_recorded())
        query.edit_message_text.assert_called_once_with(
            messages.digest_sold_ack(["BrandA Prod 1"], 1)
        )
        self.r1.refresh_from_db()
        self.assertEqual(self.r1.status, "sold_out")
        self.digest.refresh_from_db()
        self.assertIsNone(self.digest.closed_at)


class ConfirmationRequestExpiresAtTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="u_exp", password="pw")
        self.shop = Shop.objects.create(
            name="Expiry Shop",
            owner=self.user,
            telegram_chat_id="111222",
        )
        self.category = Category.objects.create(name="Phones", slug="phones")
        self.now = timezone.now()

    def test_expires_at_set_on_creation_via_send_due_digests(self):
        p = Product.objects.create(
            shop=self.shop,
            category=self.category,
            name="Phone 1",
            brand="Samsung",
            price=500.0,
            currency="ETB",
            is_available=True,
            last_confirmed_at=self.now - timedelta(days=10),
        )
        count = digest.send_due_digests()
        self.assertEqual(count, 1)
        req = ConfirmationRequest.objects.get(product=p)
        expected_expiry = p.last_confirmed_at + timedelta(
            days=settings.CONFIRMATION_HIDDEN_AFTER_DAYS
        )
        self.assertEqual(req.expires_at, expected_expiry)

    def test_expires_at_set_on_creation_via_send_confirmation_prompt(self):
        p = Product.objects.create(
            shop=self.shop,
            category=self.category,
            name="Phone 2",
            brand="Apple",
            price=800.0,
            currency="ETB",
            is_available=True,
            last_confirmed_at=self.now - timedelta(days=8),
        )
        req = send_confirmation_prompt(p, force=True)
        self.assertIsNotNone(req)
        expected_expiry = p.last_confirmed_at + timedelta(
            days=settings.CONFIRMATION_HIDDEN_AFTER_DAYS
        )
        self.assertEqual(req.expires_at, expected_expiry)

    def test_expires_at_falls_back_to_created_at_if_never_confirmed(self):
        p = Product.objects.create(
            shop=self.shop,
            category=self.category,
            name="Phone 3",
            brand="Google",
            price=700.0,
            currency="ETB",
            is_available=True,
            last_confirmed_at=None,
        )
        req = send_confirmation_prompt(p, force=True)
        self.assertIsNotNone(req)
        expected_expiry = p.created_at + timedelta(
            days=settings.CONFIRMATION_HIDDEN_AFTER_DAYS
        )
        self.assertEqual(req.expires_at, expected_expiry)


class SweepServicesTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="u_swp", password="pw")
        self.shop = Shop.objects.create(
            name="Sweep Shop",
            owner=self.user,
            telegram_chat_id="333444",
        )
        self.category = Category.objects.create(name="Tech", slug="tech")
        self.now = timezone.now()

    def test_sweep_expires_past_deadline_and_leaves_future_alone(self):
        p1 = Product.objects.create(
            shop=self.shop,
            category=self.category,
            name="Tech 1",
            brand="BrandA",
            price=100.0,
            currency="ETB",
            is_available=True,
            last_confirmed_at=self.now - timedelta(days=15),
            confirmation_status=ConfirmationStatus.STALE,
        )
        p2 = Product.objects.create(
            shop=self.shop,
            category=self.category,
            name="Tech 2",
            brand="BrandB",
            price=200.0,
            currency="ETB",
            is_available=True,
            last_confirmed_at=self.now - timedelta(days=8),
            confirmation_status=ConfirmationStatus.STALE,
        )
        dig = ConfirmationDigest.objects.create(
            shop=self.shop,
            for_date=timezone.localtime(self.now).date(),
        )
        req_expired = ConfirmationRequest.objects.create(
            product=p1,
            shop=self.shop,
            digest=dig,
            status="pending",
            expires_at=self.now - timedelta(hours=1),
        )
        req_future = ConfirmationRequest.objects.create(
            product=p2,
            shop=self.shop,
            digest=dig,
            status="pending",
            expires_at=self.now + timedelta(days=5),
        )

        summary = sweep()
        self.assertEqual(summary["expired_requests"], 1)
        self.assertEqual(summary["prompts_sent"], 0)
        self.assertEqual(summary["closed_digests"], 0)

        req_expired.refresh_from_db()
        self.assertEqual(req_expired.status, "expired")
        self.assertIsNone(req_expired.responded_at)

        req_future.refresh_from_db()
        self.assertEqual(req_future.status, "pending")

        p1.refresh_from_db()
        self.assertEqual(p1.confirmation_status, ConfirmationStatus.UNCONFIRMED)

        dig.refresh_from_db()
        self.assertIsNone(dig.closed_at)

    def test_sweep_closes_digest_when_all_requests_non_pending(self):
        p1 = Product.objects.create(
            shop=self.shop,
            category=self.category,
            name="Tech 3",
            brand="BrandC",
            price=100.0,
            currency="ETB",
            is_available=True,
            last_confirmed_at=self.now - timedelta(days=15),
        )
        dig = ConfirmationDigest.objects.create(
            shop=self.shop,
            for_date=timezone.localtime(self.now).date(),
        )
        ConfirmationRequest.objects.create(
            product=p1,
            shop=self.shop,
            digest=dig,
            status="pending",
            expires_at=self.now - timedelta(hours=1),
        )
        summary = sweep()
        self.assertEqual(summary["expired_requests"], 1)
        self.assertEqual(summary["closed_digests"], 1)

        dig.refresh_from_db()
        self.assertIsNotNone(dig.closed_at)

    def test_sweep_reconciles_status_both_directions(self):
        # Product 1: 8 days old, currently FRESH -> should become STALE
        p1 = Product.objects.create(
            shop=self.shop,
            category=self.category,
            name="Tech 4",
            brand="BrandD",
            price=100.0,
            currency="ETB",
            is_available=True,
            last_confirmed_at=self.now - timedelta(days=8),
            confirmation_status=ConfirmationStatus.FRESH,
        )
        # Product 2: 2 days old, currently STALE -> should become FRESH
        p2 = Product.objects.create(
            shop=self.shop,
            category=self.category,
            name="Tech 5",
            brand="BrandE",
            price=100.0,
            currency="ETB",
            is_available=True,
            last_confirmed_at=self.now - timedelta(days=2),
            confirmation_status=ConfirmationStatus.STALE,
        )
        # Product 3: 2 days old, currently UNCONFIRMED -> should become FRESH
        p3 = Product.objects.create(
            shop=self.shop,
            category=self.category,
            name="Tech 6",
            brand="BrandF",
            price=100.0,
            currency="ETB",
            is_available=True,
            last_confirmed_at=self.now - timedelta(days=2),
            confirmation_status=ConfirmationStatus.UNCONFIRMED,
        )

        summary = sweep()
        self.assertEqual(summary["newly_stale"], 1)
        self.assertEqual(summary["restored_fresh"], 2)

        p1.refresh_from_db()
        p2.refresh_from_db()
        p3.refresh_from_db()
        self.assertEqual(p1.confirmation_status, ConfirmationStatus.STALE)
        self.assertEqual(p2.confirmation_status, ConfirmationStatus.FRESH)
        self.assertEqual(p3.confirmation_status, ConfirmationStatus.FRESH)

    def test_sweep_sends_zero_prompts(self):
        Product.objects.create(
            shop=self.shop,
            category=self.category,
            name="Tech 7",
            brand="BrandG",
            price=100.0,
            currency="ETB",
            is_available=True,
            last_confirmed_at=self.now - timedelta(days=10),
            confirmation_status=ConfirmationStatus.STALE,
        )
        summary = sweep()
        self.assertEqual(summary["prompts_sent"], 0)
        self.assertEqual(OutboundMessage.objects.count(), 0)


class SendRemindersTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="u_rem", password="pw")
        self.shop = Shop.objects.create(
            name="Reminder Shop",
            owner=self.user,
            telegram_chat_id="555666",
        )
        self.category = Category.objects.create(name="Bags", slug="bags")
        self.now = timezone.now()
        self.today = timezone.localtime(self.now).date()

    def test_send_reminders_queues_one_reminder_for_unanswered_open_digest(self):
        digest_obj = ConfirmationDigest.objects.create(
            shop=self.shop,
            for_date=self.today - timedelta(days=2),
        )
        p1 = Product.objects.create(
            shop=self.shop,
            category=self.category,
            name="Bag 1",
            brand="Gucci",
            price=500.0,
            currency="ETB",
            is_available=True,
            last_confirmed_at=self.now - timedelta(days=9),
        )
        ConfirmationRequest.objects.create(
            product=p1,
            shop=self.shop,
            digest=digest_obj,
            status="pending",
        )

        count = send_reminders()
        self.assertEqual(count, 1)

        digest_obj.refresh_from_db()
        self.assertIsNotNone(digest_obj.reminded_at)

        outbox_msg = OutboundMessage.objects.get(
            dedupe_key=f"reminder:{digest_obj.pk}:{digest_obj.for_date}"
        )
        self.assertEqual(outbox_msg.kind, "reminder")
        self.assertEqual(outbox_msg.chat_id, "555666")
        self.assertEqual(outbox_msg.payload["digest_id"], digest_obj.pk)
        self.assertIn("Friendly reminder for Reminder Shop", outbox_msg.payload["text"])

        # Second run is a no-op (idempotent)
        second_count = send_reminders()
        self.assertEqual(second_count, 0)

    def test_send_reminders_ignores_answered_digests(self):
        digest_obj = ConfirmationDigest.objects.create(
            shop=self.shop,
            for_date=self.today - timedelta(days=2),
        )
        p1 = Product.objects.create(
            shop=self.shop,
            category=self.category,
            name="Bag 2",
            brand="Gucci",
            price=500.0,
            currency="ETB",
            is_available=True,
            last_confirmed_at=self.now - timedelta(days=9),
        )
        p2 = Product.objects.create(
            shop=self.shop,
            category=self.category,
            name="Bag 3",
            brand="Gucci",
            price=600.0,
            currency="ETB",
            is_available=True,
            last_confirmed_at=self.now - timedelta(days=9),
        )
        ConfirmationRequest.objects.create(
            product=p1,
            shop=self.shop,
            digest=digest_obj,
            status="confirmed",
        )
        ConfirmationRequest.objects.create(
            product=p2,
            shop=self.shop,
            digest=digest_obj,
            status="pending",
        )

        count = send_reminders()
        self.assertEqual(count, 0)

    def test_send_reminders_ignores_closed_digests(self):
        digest_obj = ConfirmationDigest.objects.create(
            shop=self.shop,
            for_date=self.today - timedelta(days=2),
            closed_at=self.now,
        )
        p1 = Product.objects.create(
            shop=self.shop,
            category=self.category,
            name="Bag 4",
            brand="Prada",
            price=500.0,
            currency="ETB",
            is_available=True,
            last_confirmed_at=self.now - timedelta(days=9),
        )
        ConfirmationRequest.objects.create(
            product=p1,
            shop=self.shop,
            digest=digest_obj,
            status="pending",
        )
        count = send_reminders()
        self.assertEqual(count, 0)

    def test_send_reminders_ignores_outside_window_digests(self):
        digest_obj = ConfirmationDigest.objects.create(
            shop=self.shop,
            for_date=self.today - timedelta(days=10),
        )
        p1 = Product.objects.create(
            shop=self.shop,
            category=self.category,
            name="Bag 5",
            brand="Chanel",
            price=500.0,
            currency="ETB",
            is_available=True,
            last_confirmed_at=self.now - timedelta(days=17),
        )
        ConfirmationRequest.objects.create(
            product=p1,
            shop=self.shop,
            digest=digest_obj,
            status="pending",
        )
        count = send_reminders()
        self.assertEqual(count, 0)

    def test_send_reminders_ignores_blocked_or_unlinked_shops(self):
        self.shop.blocked_at = self.now
        self.shop.save(update_fields=["blocked_at"])

        digest_obj = ConfirmationDigest.objects.create(
            shop=self.shop,
            for_date=self.today - timedelta(days=2),
        )
        p1 = Product.objects.create(
            shop=self.shop,
            category=self.category,
            name="Bag 6",
            brand="Dior",
            price=500.0,
            currency="ETB",
            is_available=True,
            last_confirmed_at=self.now - timedelta(days=9),
        )
        ConfirmationRequest.objects.create(
            product=p1,
            shop=self.shop,
            digest=digest_obj,
            status="pending",
        )
        count = send_reminders()
        self.assertEqual(count, 0)


class PreviewDigestCommandTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="merchant", password="password123"
        )
        self.shop = Shop.objects.create(
            name="Bole Electronics",
            slug="bole-electronics",
            owner=self.user,
            telegram_chat_id="987654321",
        )
        self.category = Category.objects.create(name="Phones", slug="phones")
        self.now = timezone.now()
        self.today = timezone.localtime(self.now).date()
        # Create a due product
        self.product = Product.objects.create(
            shop=self.shop,
            category=self.category,
            name="Galaxy S23",
            brand="Samsung",
            price=1000.0,
            currency="ETB",
            is_available=True,
            last_confirmed_at=self.now - timedelta(days=10),
        )

    def test_preview_creates_no_database_rows(self):
        counts_before = {
            "digest": ConfirmationDigest.objects.count(),
            "request": ConfirmationRequest.objects.count(),
            "outbox": OutboundMessage.objects.count(),
            "product": Product.objects.count(),
            "shop": Shop.objects.count(),
        }

        out = io.StringIO()
        call_command("preview_digest", self.shop.slug, stdout=out)

        counts_after = {
            "digest": ConfirmationDigest.objects.count(),
            "request": ConfirmationRequest.objects.count(),
            "outbox": OutboundMessage.objects.count(),
            "product": Product.objects.count(),
            "shop": Shop.objects.count(),
        }
        self.assertEqual(counts_before, counts_after)

    def test_due_shop_prints_items_and_would_send(self):
        out = io.StringIO()
        call_command("preview_digest", str(self.shop.pk), stdout=out)
        output = out.getvalue()

        self.assertIn("Shop: Bole Electronics", output)
        self.assertIn("Samsung Galaxy S23", output)
        self.assertIn("1,000.00 ETB", output)
        self.assertIn("Buttons: ✅ All still available", output)
        self.assertIn(f"Dedupe key: digest:{self.shop.pk}:{self.today}", output)
        self.assertIn("Verdict: WOULD SEND", output)

    def test_shop_with_recent_digest_prints_would_skip_with_cadence_reason(self):
        ConfirmationDigest.objects.create(
            shop=self.shop,
            for_date=self.today - timedelta(days=2),
        )

        out = io.StringIO()
        call_command("preview_digest", self.shop.slug, stdout=out)
        output = out.getvalue()

        self.assertIn("Verdict: WOULD SKIP (cadence: last digest was on", output)
        self.assertIn("2d < 7d", output)

    def test_all_flag_lists_due_shops_and_skips_the_rest(self):
        # shop2: blocked -> not candidate
        Shop.objects.create(
            name="Blocked Shop",
            slug="blocked-shop",
            owner=self.user,
            telegram_chat_id="111222",
            blocked_at=self.now,
        )
        # shop3: linked, unblocked, but recent digest -> candidate, but cadence skip
        shop3 = Shop.objects.create(
            name="Recent Shop",
            slug="recent-shop",
            owner=self.user,
            telegram_chat_id="333444",
        )
        ConfirmationDigest.objects.create(
            shop=shop3,
            for_date=self.today - timedelta(days=3),
        )

        out = io.StringIO()
        call_command("preview_digest", "--all", stdout=out)
        output = out.getvalue()

        # shop is due -> listed in summary as WOULD SEND and in full block
        self.assertIn(f"- Bole Electronics ({self.shop.pk}): WOULD SEND", output)
        # shop3 has recent digest -> listed in summary as WOULD SKIP
        self.assertIn(f"- Recent Shop ({shop3.pk}): WOULD SKIP (cadence:", output)
        # shop2 is blocked -> not even a candidate
        self.assertNotIn("Blocked Shop", output)

        # Full block rendered only for would_send (Bole Electronics)
        self.assertIn("Full digest preview for 1 shop(s) that WOULD SEND:", output)
        self.assertIn("Samsung Galaxy S23", output)

    def test_argument_validation(self):
        with self.assertRaises(CommandError):
            call_command("preview_digest")  # neither
        with self.assertRaises(CommandError):
            call_command("preview_digest", self.shop.slug, "--all")  # both
        with self.assertRaises(CommandError):
            call_command("preview_digest", "non-existent-shop-slug")
        with self.assertRaises(CommandError):
            call_command("preview_digest", self.shop.slug, "--date", "not-a-date")


@override_settings(ENABLE_TELEGRAM_BOT_PROMPTS=False)
class PromptsDisabledTests(TestCase):
    """The master outbound flag must stop the digest and reminder jobs."""

    def setUp(self):
        self.user = User.objects.create_user(username="pd", password="pw")
        self.shop = Shop.objects.create(
            name="PD Shop",
            slug="pd-shop",
            owner=self.user,
            telegram_chat_id="777000",
        )
        self.category = Category.objects.create(name="Phones PD", slug="phones-pd")
        self.product = Product.objects.create(
            shop=self.shop,
            category=self.category,
            brand="Brand",
            name="Device",
            price="100.00",
            is_available=True,
            last_confirmed_at=timezone.now() - timedelta(days=10),
        )

    def test_send_due_digests_disabled_writes_nothing(self):
        self.assertEqual(digest.send_due_digests(), 0)
        self.assertEqual(ConfirmationDigest.objects.count(), 0)
        self.assertEqual(ConfirmationRequest.objects.count(), 0)
        self.assertEqual(OutboundMessage.objects.count(), 0)

    def test_send_reminders_disabled_returns_zero(self):
        self.assertEqual(send_reminders(), 0)
        self.assertEqual(OutboundMessage.objects.count(), 0)
