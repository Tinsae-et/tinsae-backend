from datetime import timedelta
from io import StringIO
from unittest import mock

from asgiref.sync import async_to_sync
from django.contrib.auth import get_user_model
from django.core.management import CommandError, call_command
from django.db import IntegrityError, transaction
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from bot import telegram_api as telegram
from bot.management.commands.run_bot import on_callback
from bot.models import ConfirmationRequest
from bot.services import handle_telegram_callback, send_confirmation_prompt
from catalog.models import Category, ConfirmationStatus, Product
from shops.models import Shop

User = get_user_model()

CHAT_ID = "987654321"
OTHER_CHAT_ID = "111000111"


@override_settings(TELEGRAM_BOT_TOKEN="test-token", ENABLE_TELEGRAM_BOT_PROMPTS=True)
class BotTestCase(TestCase):
    """Shared fixture.

    The token stays set so the real send path is exercised — overriding it to None
    would run the mock branch, which is the one we don't ship. The transport is
    patched per-test instead, and setUp installs a tripwire so a test that forgets
    to patch fails loudly rather than reaching the network.
    """

    def setUp(self):
        self.user = User.objects.create_user(
            username="merchant",
            email="merchant@example.com",
            password="password123",
        )
        self.shop = Shop.objects.create(
            name="Bole Electronics", owner=self.user, telegram_chat_id=CHAT_ID
        )
        self.shop_no_tg = Shop.objects.create(
            name="Offline Shop", owner=self.user, telegram_chat_id=""
        )
        self.category = Category.objects.create(name="Smartphones", slug="smartphones")

        now = timezone.now()
        self.fresh_product = self._product(
            "iPhone 15", "iphone-15", self.shop, now - timedelta(days=5)
        )
        self.stale_product = self._product(
            "Galaxy S23", "galaxy-s23", self.shop, now - timedelta(days=40)
        )
        self.unconfirmed_product = self._product(
            "Headphones", "sony-headphones", self.shop_no_tg, None
        )
        # created_at is auto_now_add, so backdate it to make this one genuinely old.
        Product.objects.filter(pk=self.unconfirmed_product.pk).update(
            created_at=now - timedelta(days=40)
        )

        tripwire = mock.patch(
            "bot.telegram_api._call",
            side_effect=AssertionError("Telegram transport was not patched"),
        )
        tripwire.start()
        self.addCleanup(tripwire.stop)

    def _product(self, name, slug, shop, last_confirmed_at):
        return Product.objects.create(
            shop=shop,
            category=self.category,
            brand="Test",
            name=name,
            slug=slug,
            price="10000.00",
            last_confirmed_at=last_confirmed_at,
            is_available=True,
        )


class SendPromptTests(BotTestCase):
    def test_payload_is_addressed_to_the_owning_shop(self):
        with mock.patch(
            "bot.telegram_api.send_message", return_value={"message_id": 4242}
        ) as send:
            request = send_confirmation_prompt(self.stale_product)

        send.assert_called_once()
        kwargs = send.call_args.kwargs
        self.assertEqual(kwargs["chat_id"], CHAT_ID)
        self.assertIn(self.stale_product.name, kwargs["text"])
        buttons = kwargs["reply_markup"]["inline_keyboard"][0]
        self.assertEqual(
            [b["callback_data"] for b in buttons],
            [f"confirm_{request.pk}", f"deny_{request.pk}"],
        )

    def test_message_id_is_persisted(self):
        with mock.patch("bot.telegram_api.send_message", return_value={"message_id": 4242}):
            request = send_confirmation_prompt(self.stale_product)
        request.refresh_from_db()
        self.assertEqual(request.telegram_message_id, "4242")

    def test_failed_send_leaves_no_pending_request(self):
        with mock.patch(
            "bot.telegram_api.send_message",
            side_effect=telegram.TelegramError("network down"),
        ):
            self.assertIsNone(send_confirmation_prompt(self.stale_product))
        self.assertEqual(ConfirmationRequest.objects.count(), 0)

    def test_shop_without_telegram_is_skipped(self):
        self.assertIsNone(send_confirmation_prompt(self.unconfirmed_product))
        self.assertEqual(ConfirmationRequest.objects.count(), 0)

    def test_duplicate_prompt_is_skipped(self):
        with mock.patch(
            "bot.telegram_api.send_message", return_value={"message_id": 1}
        ) as send:
            self.assertIsNotNone(send_confirmation_prompt(self.stale_product))
            self.assertIsNone(send_confirmation_prompt(self.stale_product))
        self.assertEqual(send.call_count, 1)

    @override_settings(ENABLE_TELEGRAM_BOT_PROMPTS=False)
    def test_disabled_flag_creates_no_request(self):
        self.assertIsNone(send_confirmation_prompt(self.stale_product))
        self.assertEqual(ConfirmationRequest.objects.count(), 0)

    def test_unavailable_product_gets_a_back_in_stock_prompt(self):
        Product.objects.filter(pk=self.stale_product.pk).update(is_available=False)
        self.stale_product.refresh_from_db()
        with mock.patch(
            "bot.telegram_api.send_message", return_value={"message_id": 1}
        ) as send:
            send_confirmation_prompt(self.stale_product)
        self.assertIn("back in stock", send.call_args.kwargs["text"])

    def test_database_rejects_a_second_pending_request(self):
        ConfirmationRequest.objects.create(
            product=self.stale_product, shop=self.shop, status="pending"
        )
        with self.assertRaises(IntegrityError), transaction.atomic():
            ConfirmationRequest.objects.create(
                product=self.stale_product, shop=self.shop, status="pending"
            )

    def test_resolved_requests_do_not_collide(self):
        """The constraint is partial — only `pending` rows are unique per product."""
        ConfirmationRequest.objects.create(
            product=self.stale_product, shop=self.shop, status="confirmed"
        )
        ConfirmationRequest.objects.create(
            product=self.stale_product, shop=self.shop, status="denied"
        )
        self.assertEqual(ConfirmationRequest.objects.count(), 2)


class CallbackTests(BotTestCase):
    def _request(self, status="pending"):
        return ConfirmationRequest.objects.create(
            product=self.stale_product, shop=self.shop, status=status
        )

    def test_confirm_marks_product_fresh(self):
        request = self._request()
        self.assertTrue(
            handle_telegram_callback(f"confirm_{request.pk}", chat_id=CHAT_ID)
        )
        request.refresh_from_db()
        self.stale_product.refresh_from_db()
        self.assertEqual(request.status, "confirmed")
        self.assertEqual(self.stale_product.confirmation_status, "fresh")
        self.assertTrue(self.stale_product.is_available)

    def test_deny_marks_product_unavailable(self):
        request = self._request()
        self.assertTrue(handle_telegram_callback(f"deny_{request.pk}", chat_id=CHAT_ID))
        request.refresh_from_db()
        self.stale_product.refresh_from_db()
        self.assertEqual(request.status, "denied")
        self.assertFalse(self.stale_product.is_available)

    def test_deny_restarts_the_confirmation_clock(self):
        """Out of stock is fresh information, not the absence of information."""
        request = self._request()
        handle_telegram_callback(f"deny_{request.pk}", chat_id=CHAT_ID)
        self.stale_product.refresh_from_db()
        self.assertEqual(
            self.stale_product.confirmation_status, ConfirmationStatus.FRESH
        )
        self.assertGreater(
            self.stale_product.last_confirmed_at, timezone.now() - timedelta(minutes=1)
        )

    def test_callback_from_another_chat_is_rejected(self):
        request = self._request()
        before = self.stale_product.confirmation_status
        self.assertFalse(
            handle_telegram_callback(f"confirm_{request.pk}", chat_id=OTHER_CHAT_ID)
        )
        request.refresh_from_db()
        self.stale_product.refresh_from_db()
        self.assertEqual(request.status, "pending")
        self.assertEqual(self.stale_product.confirmation_status, before)

    def test_confirmed_request_cannot_be_replayed(self):
        request = self._request(status="confirmed")
        self.assertFalse(
            handle_telegram_callback(f"confirm_{request.pk}", chat_id=CHAT_ID)
        )

    def test_malformed_callback_is_rejected(self):
        self.assertFalse(handle_telegram_callback("nonsense", chat_id=CHAT_ID))
        self.assertFalse(handle_telegram_callback("confirm_abc", chat_id=CHAT_ID))


class SweepTests(BotTestCase):
    def test_sweep_marks_stale_and_prompts_once(self):
        out = StringIO()
        with mock.patch("bot.telegram_api.send_message", return_value={"message_id": 1}):
            call_command("sweep_freshness", "--staleness-days=30", stdout=out)
        output = out.getvalue()

        self.assertIn("[Freshness Sweep Complete]", output)
        self.assertIn("Stale products: 2", output)
        self.assertIn("Prompts sent: 1", output)

        self.fresh_product.refresh_from_db()
        self.stale_product.refresh_from_db()
        self.assertEqual(self.fresh_product.confirmation_status, "fresh")
        self.assertEqual(self.stale_product.confirmation_status, "stale")

    def test_newly_listed_product_is_not_stale(self):
        new_product = self._product("Pixel 9", "pixel-9", self.shop, None)
        with mock.patch("bot.telegram_api.send_message", return_value={"message_id": 1}):
            call_command("sweep_freshness", stdout=StringIO())
        new_product.refresh_from_db()
        self.assertEqual(new_product.confirmation_status, "fresh")
        self.assertFalse(
            ConfirmationRequest.objects.filter(product=new_product).exists()
        )

    def test_sweep_is_idempotent(self):
        with mock.patch("bot.telegram_api.send_message", return_value={"message_id": 1}):
            call_command("sweep_freshness", stdout=StringIO())
            out = StringIO()
            call_command("sweep_freshness", stdout=out)
        self.assertIn("Prompts sent: 0", out.getvalue())
        self.assertEqual(
            ConfirmationRequest.objects.filter(product=self.stale_product).count(), 1
        )

    def test_expires_old_pending_requests(self):
        old = ConfirmationRequest.objects.create(
            product=self.stale_product, shop=self.shop, status="pending"
        )
        ConfirmationRequest.objects.filter(pk=old.pk).update(
            created_at=timezone.now() - timedelta(hours=50)
        )
        out = StringIO()
        with mock.patch("bot.telegram_api.send_message", return_value={"message_id": 1}):
            call_command("sweep_freshness", "--expiry-hours=48", stdout=out)
        self.assertIn("Expired requests: 1", out.getvalue())
        old.refresh_from_db()
        self.assertEqual(old.status, "expired")

    def test_sweep_restores_a_product_that_is_no_longer_stale(self):
        """The reconcile runs both ways, so a threshold change self-corrects."""
        Product.objects.filter(pk=self.fresh_product.pk).update(
            confirmation_status=ConfirmationStatus.STALE
        )
        out = StringIO()
        with mock.patch("bot.telegram_api.send_message", return_value={"message_id": 1}):
            call_command("sweep_freshness", stdout=out)
        self.fresh_product.refresh_from_db()
        self.assertEqual(self.fresh_product.confirmation_status, "fresh")
        self.assertIn("Restored to fresh: 1", out.getvalue())

    def test_denied_product_is_swept_and_reprompted(self):
        """A product marked out of stock must not fall out of the loop forever."""
        Product.objects.filter(pk=self.stale_product.pk).update(is_available=False)
        with mock.patch(
            "bot.telegram_api.send_message", return_value={"message_id": 1}
        ) as send:
            call_command("sweep_freshness", stdout=StringIO())
        self.stale_product.refresh_from_db()
        self.assertEqual(self.stale_product.confirmation_status, "stale")
        send.assert_called_once()

    def test_staleness_threshold_comes_from_settings(self):
        with override_settings(PRODUCT_STALENESS_DAYS=90):
            with mock.patch(
                "bot.telegram_api.send_message", return_value={"message_id": 1}
            ):
                call_command("sweep_freshness", stdout=StringIO())
        self.stale_product.refresh_from_db()
        self.assertEqual(self.stale_product.confirmation_status, "fresh")

    def test_dry_run_mutates_nothing(self):
        out = StringIO()
        call_command("sweep_freshness", "--dry-run", stdout=out)
        self.assertIn("[DRY RUN]", out.getvalue())
        self.stale_product.refresh_from_db()
        self.assertEqual(self.stale_product.confirmation_status, "fresh")
        self.assertEqual(ConfirmationRequest.objects.count(), 0)


class RunBotCommandTest(SimpleTestCase):
    def test_run_bot_requires_token(self):
        with override_settings(TELEGRAM_BOT_TOKEN=""):
            with self.assertRaisesMessage(
                CommandError, "TELEGRAM_BOT_TOKEN is not set"
            ):
                call_command("run_bot")


class CallbackHandlerTests(BotTestCase):
    """PTB handler wiring only — the state change itself is CallbackTests' job."""

    def _update(self, data, chat_id=CHAT_ID):
        query = mock.AsyncMock()
        query.data = data
        query.message.chat.id = int(chat_id)
        update = mock.Mock()
        update.callback_query = query
        return update, query

    def test_handler_applies_a_confirmation(self):
        request = ConfirmationRequest.objects.create(
            product=self.stale_product, shop=self.shop, status="pending"
        )
        update, query = self._update(f"confirm_{request.pk}")
        async_to_sync(on_callback)(update, None)

        request.refresh_from_db()
        self.assertEqual(request.status, "confirmed")
        query.answer.assert_awaited_with("Response recorded")
        query.edit_message_text.assert_awaited()

    def test_handler_reports_an_inactive_request(self):
        request = ConfirmationRequest.objects.create(
            product=self.stale_product, shop=self.shop, status="confirmed"
        )
        update, query = self._update(f"confirm_{request.pk}")
        async_to_sync(on_callback)(update, None)

        query.answer.assert_awaited_with("This request is no longer active.")
        query.edit_message_text.assert_not_awaited()

    def test_handler_rejects_a_callback_from_another_chat(self):
        request = ConfirmationRequest.objects.create(
            product=self.stale_product, shop=self.shop, status="pending"
        )
        update, query = self._update(f"confirm_{request.pk}", chat_id=OTHER_CHAT_ID)
        async_to_sync(on_callback)(update, None)

        request.refresh_from_db()
        self.assertEqual(request.status, "pending")
