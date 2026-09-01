from datetime import timedelta
from io import StringIO
from unittest import mock

from asgiref.sync import async_to_sync
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management import CommandError, call_command
from django.db import IntegrityError, transaction
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from bot import telegram_api as telegram
from bot.management.commands.run_bot import my_products, on_callback, on_text
from bot.models import ConfirmationRequest
from bot.selectors import listing_for_chat, shops_for_chat
from bot.services import (
    handle_product_callback,
    handle_telegram_callback,
    handle_text_reply,
    parse_answer,
    send_confirmation_prompt,
)
from catalog.models import Category, ConfirmationStatus, Product
from catalog.services import apply_confirmation
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

    def test_prompt_names_the_product_and_its_price(self):
        with mock.patch(
            "bot.telegram_api.send_message", return_value={"message_id": 1}
        ) as send:
            send_confirmation_prompt(self.stale_product)

        text = send.call_args.kwargs["text"]
        self.assertIn("Test Galaxy S23", text)
        self.assertIn("10,000.00 ETB", text)
        self.assertIn(self.shop.name, text)

    def test_prompt_offers_both_a_button_and_a_typed_answer(self):
        with mock.patch(
            "bot.telegram_api.send_message", return_value={"message_id": 1}
        ) as send:
            send_confirmation_prompt(self.stale_product)

        kwargs = send.call_args.kwargs
        labels = [b["text"] for b in kwargs["reply_markup"]["inline_keyboard"][0]]
        self.assertEqual(labels, ["✅ Still available", "❌ Sold out"])
        self.assertIn("reply YES or NO", kwargs["text"])

    def test_the_yes_no_the_prompt_advertises_actually_parses(self):
        """The prompt makes a promise about YES/NO; hold it to that promise."""
        self.assertIs(parse_answer("YES"), True)
        self.assertIs(parse_answer("NO"), False)

    def test_message_id_is_persisted(self):
        with mock.patch(
            "bot.telegram_api.send_message", return_value={"message_id": 4242}
        ):
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

    @override_settings(ENABLE_TELEGRAM_BOT_PROMPTS=False)
    def test_force_lifts_the_policy_flag_and_nothing_else(self):
        """The other two gates are not policy — one has nowhere to send, the
        other is the double-send guard."""
        with mock.patch(
            "bot.telegram_api.send_message", return_value={"message_id": 1}
        ) as send:
            self.assertIsNotNone(
                send_confirmation_prompt(self.stale_product, force=True)
            )
            # Unlinked shop: no chat to send to.
            self.assertIsNone(
                send_confirmation_prompt(self.unconfirmed_product, force=True)
            )
            # Already asked.
            self.assertIsNone(send_confirmation_prompt(self.stale_product, force=True))

        self.assertEqual(send.call_count, 1)

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
            product=self.stale_product, shop=self.shop, status="sold_out"
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
        self.assertEqual(request.status, "sold_out")
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

    def test_new_request_is_unanswered_on_the_default_channel(self):
        request = self._request()
        self.assertIsNone(request.responded_at)
        self.assertEqual(request.channel, "telegram")

    def test_confirm_records_responded_at(self):
        request = self._request()
        handle_telegram_callback(f"confirm_{request.pk}", chat_id=CHAT_ID)
        request.refresh_from_db()
        self.assertIsNotNone(request.responded_at)

    def test_deny_records_responded_at(self):
        request = self._request()
        handle_telegram_callback(f"deny_{request.pk}", chat_id=CHAT_ID)
        request.refresh_from_db()
        self.assertIsNotNone(request.responded_at)


class SweepTests(BotTestCase):
    def test_sweep_marks_stale_and_prompts_once(self):
        out = StringIO()
        with mock.patch(
            "bot.telegram_api.send_message", return_value={"message_id": 1}
        ):
            call_command("sweep_freshness", "--staleness-days=30", stdout=out)
        output = out.getvalue()

        self.assertIn("[Freshness Sweep Complete]", output)
        self.assertIn("Stale products: 2", output)
        self.assertIn("Prompts sent: 1", output)

        self.fresh_product.refresh_from_db()
        self.stale_product.refresh_from_db()
        self.assertEqual(self.fresh_product.confirmation_status, "fresh")
        self.assertEqual(self.stale_product.confirmation_status, "stale")

    def test_never_confirmed_product_is_stale(self):
        """Never confirmed is stale outright — an unbacked claim is not evidence."""
        new_product = self._product("Pixel 9", "pixel-9", self.shop, None)
        with mock.patch(
            "bot.telegram_api.send_message", return_value={"message_id": 1}
        ):
            call_command("sweep_freshness", stdout=StringIO())
        new_product.refresh_from_db()
        self.assertEqual(new_product.confirmation_status, "stale")
        self.assertTrue(
            ConfirmationRequest.objects.filter(product=new_product).exists()
        )

    def test_sweep_is_idempotent(self):
        with mock.patch(
            "bot.telegram_api.send_message", return_value={"message_id": 1}
        ):
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
        with mock.patch(
            "bot.telegram_api.send_message", return_value={"message_id": 1}
        ):
            call_command("sweep_freshness", "--expiry-hours=48", stdout=out)
        self.assertIn("Expired requests: 1", out.getvalue())
        old.refresh_from_db()
        self.assertEqual(old.status, "expired")
        # A timeout is the absence of an answer, so it leaves no response time.
        self.assertIsNone(old.responded_at)
        # And the silence is recorded on the product, not only on the request.
        self.stale_product.refresh_from_db()
        self.assertEqual(
            self.stale_product.confirmation_status,
            ConfirmationStatus.UNCONFIRMED,
        )

    def test_a_timed_out_product_is_not_downgraded_by_the_same_sweep(self):
        """Step 1 writes UNCONFIRMED; step 2 must not walk back over it.

        Both values satisfy `needing_confirmation`, so the reconcile pass sees a
        product step 1 just marked and would set it to STALE on the very same
        run -- losing the one piece of evidence the timeout produced.
        """
        old = ConfirmationRequest.objects.create(
            product=self.stale_product, shop=self.shop, status="pending"
        )
        ConfirmationRequest.objects.filter(pk=old.pk).update(
            created_at=timezone.now() - timedelta(hours=50)
        )
        with mock.patch(
            "bot.telegram_api.send_message", return_value={"message_id": 1}
        ):
            call_command("sweep_freshness", "--expiry-hours=48", stdout=StringIO())

        self.stale_product.refresh_from_db()
        self.assertEqual(
            self.stale_product.confirmation_status,
            ConfirmationStatus.UNCONFIRMED,
        )

    def test_answering_clears_unconfirmed(self):
        """The only way back up the ladder is a merchant's answer."""
        Product.objects.filter(pk=self.stale_product.pk).update(
            confirmation_status=ConfirmationStatus.UNCONFIRMED
        )
        self.stale_product.refresh_from_db()

        apply_confirmation(self.stale_product, confirmed=True)

        self.stale_product.refresh_from_db()
        self.assertEqual(
            self.stale_product.confirmation_status, ConfirmationStatus.FRESH
        )

    def test_an_unconfirmed_product_is_still_prompted(self):
        """Going silent must not remove a merchant from the loop for good."""
        Product.objects.filter(pk=self.stale_product.pk).update(
            confirmation_status=ConfirmationStatus.UNCONFIRMED
        )
        out = StringIO()
        with mock.patch(
            "bot.telegram_api.send_message", return_value={"message_id": 1}
        ):
            call_command("sweep_freshness", stdout=out)

        self.assertIn("Prompts sent: 1", out.getvalue())

    def test_sweep_restores_a_product_that_is_no_longer_stale(self):
        """The reconcile runs both ways, so a threshold change self-corrects."""
        Product.objects.filter(pk=self.fresh_product.pk).update(
            confirmation_status=ConfirmationStatus.STALE
        )
        out = StringIO()
        with mock.patch(
            "bot.telegram_api.send_message", return_value={"message_id": 1}
        ):
            call_command("sweep_freshness", stdout=out)
        self.fresh_product.refresh_from_db()
        self.assertEqual(self.fresh_product.confirmation_status, "fresh")
        self.assertIn("Restored to fresh: 1", out.getvalue())

    def test_unavailable_product_is_not_swept(self):
        """The sweep tracks only what the merchant still lists as available."""
        Product.objects.filter(pk=self.stale_product.pk).update(is_available=False)
        with mock.patch(
            "bot.telegram_api.send_message", return_value={"message_id": 1}
        ) as send:
            call_command("sweep_freshness", stdout=StringIO())
        self.stale_product.refresh_from_db()
        self.assertEqual(self.stale_product.confirmation_status, "fresh")
        send.assert_not_called()

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

    @override_settings(ENABLE_TELEGRAM_BOT_PROMPTS=False)
    def test_dry_run_reports_no_prompts_when_sending_is_disabled(self):
        """The dry run must apply the same policy gate as the real send path."""
        out = StringIO()
        call_command("sweep_freshness", "--dry-run", stdout=out)
        self.assertIn("Prompts sent: 0", out.getvalue())

    def test_dry_run_counts_a_prompt_the_expiry_pass_would_unblock(self):
        """A real run expires first, so a doomed pending row must not mask it."""
        old = ConfirmationRequest.objects.create(
            product=self.stale_product, shop=self.shop, status="pending"
        )
        ConfirmationRequest.objects.filter(pk=old.pk).update(
            created_at=timezone.now() - timedelta(hours=50)
        )
        out = StringIO()
        call_command("sweep_freshness", "--dry-run", stdout=out)
        self.assertIn("Expired requests: 1", out.getvalue())
        self.assertIn("Prompts sent: 1", out.getvalue())


class DashboardAnswersCloseOutstandingPrompts(BotTestCase):
    """A merchant answers once, through whichever channel is in front of them.

    The bot prompts, then the merchant opens the dashboard instead of tapping
    the button. Both are answers, and only one of them was being recorded.
    """

    def test_confirming_in_the_dashboard_resolves_a_pending_prompt(self):
        request = ConfirmationRequest.objects.create(
            product=self.stale_product, shop=self.shop, status="pending"
        )
        apply_confirmation(self.stale_product, confirmed=True)

        request.refresh_from_db()
        self.assertEqual(request.status, "confirmed")
        self.assertIsNotNone(request.responded_at)

    def test_marking_sold_out_in_the_dashboard_resolves_it_too(self):
        request = ConfirmationRequest.objects.create(
            product=self.stale_product, shop=self.shop, status="pending"
        )
        apply_confirmation(self.stale_product, confirmed=False)

        request.refresh_from_db()
        self.assertEqual(request.status, "sold_out")


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


class ListingSelectorTests(BotTestCase):
    """What /myproducts shows, and who it shows it to."""

    def test_listing_is_scoped_to_the_calling_chat(self):
        shops, products = listing_for_chat(CHAT_ID)

        self.assertEqual([s.pk for s in shops], [self.shop.pk])
        self.assertEqual([p.pk for p in products], [self.stale_product.pk])

    def test_an_unlinked_chat_gets_nothing(self):
        self.assertEqual(listing_for_chat(OTHER_CHAT_ID), ([], []))
        self.assertEqual(shops_for_chat(""), [])
        self.assertEqual(shops_for_chat(None), [])

    def test_never_confirmed_products_lead_the_list(self):
        never = self._product("Pixel 9", "pixel-9", self.shop, None)

        _, products = listing_for_chat(CHAT_ID)

        self.assertEqual([p.pk for p in products], [never.pk, self.stale_product.pk])

    def test_fresh_and_unavailable_products_are_excluded(self):
        Product.objects.filter(pk=self.stale_product.pk).update(is_available=False)

        _, products = listing_for_chat(CHAT_ID)

        self.assertEqual(products, [])

    def test_one_chat_running_two_shops_sees_both(self):
        second = Shop.objects.create(
            name="Piassa Electronics", owner=self.user, telegram_chat_id=CHAT_ID
        )
        other = self._product(
            "Nokia 3310", "nokia-3310", second, timezone.now() - timedelta(days=40)
        )

        shops, products = listing_for_chat(CHAT_ID)

        self.assertEqual(len(shops), 2)
        self.assertCountEqual(
            [p.pk for p in products], [self.stale_product.pk, other.pk]
        )


class ProductCallbackTests(BotTestCase):
    """The product-keyed answers behind the /myproducts buttons."""

    def test_confirm_marks_the_product_fresh_and_available(self):
        self.assertIsNotNone(
            handle_product_callback(f"pconfirm:{self.stale_product.pk}:0", CHAT_ID)
        )

        self.stale_product.refresh_from_db()
        self.assertEqual(
            self.stale_product.confirmation_status, ConfirmationStatus.FRESH
        )
        self.assertTrue(self.stale_product.is_available)
        self.assertGreater(
            self.stale_product.last_confirmed_at, timezone.now() - timedelta(minutes=1)
        )

    def test_deny_marks_it_unavailable_but_freshly_known(self):
        handle_product_callback(f"pdeny:{self.stale_product.pk}:0", CHAT_ID)

        self.stale_product.refresh_from_db()
        self.assertFalse(self.stale_product.is_available)
        self.assertEqual(
            self.stale_product.confirmation_status, ConfirmationStatus.FRESH
        )

    def test_it_works_with_no_confirmation_request_at_all(self):
        """The listing shows products that were never prompted — the common case."""
        self.assertEqual(ConfirmationRequest.objects.count(), 0)

        self.assertIsNotNone(
            handle_product_callback(f"pconfirm:{self.stale_product.pk}:0", CHAT_ID)
        )

    def test_an_outstanding_prompt_for_the_same_product_is_resolved(self):
        request = ConfirmationRequest.objects.create(
            product=self.stale_product, shop=self.shop, status="pending"
        )

        handle_product_callback(f"pconfirm:{self.stale_product.pk}:0", CHAT_ID)

        request.refresh_from_db()
        self.assertEqual(request.status, "confirmed")

    def test_another_chat_cannot_answer(self):
        before = self.stale_product.last_confirmed_at

        self.assertIsNone(
            handle_product_callback(
                f"pconfirm:{self.stale_product.pk}:0", OTHER_CHAT_ID
            )
        )

        self.stale_product.refresh_from_db()
        self.assertEqual(self.stale_product.last_confirmed_at, before)

    def test_a_shop_with_no_chat_id_cannot_be_answered_by_an_empty_chat(self):
        self.assertIsNone(
            handle_product_callback(f"pconfirm:{self.unconfirmed_product.pk}:0", "")
        )

    def test_malformed_and_unknown_callbacks_are_rejected(self):
        self.assertIsNone(handle_product_callback("pconfirm:abc:0", CHAT_ID))
        self.assertIsNone(handle_product_callback("nonsense", CHAT_ID))
        self.assertIsNone(handle_product_callback("pconfirm:99999999:0", CHAT_ID))

    def test_a_stale_button_cannot_republish_an_out_of_stock_product(self):
        """No back door into the re-stock path we have not built."""
        Product.objects.filter(pk=self.stale_product.pk).update(is_available=False)

        self.assertIsNone(
            handle_product_callback(f"pconfirm:{self.stale_product.pk}:0", CHAT_ID)
        )

        self.stale_product.refresh_from_db()
        self.assertFalse(self.stale_product.is_available)

    def test_a_stale_deny_button_is_still_honoured(self):
        """Re-denying is idempotent and cannot resurrect anything."""
        Product.objects.filter(pk=self.stale_product.pk).update(is_available=False)

        self.assertIsNotNone(
            handle_product_callback(f"pdeny:{self.stale_product.pk}:0", CHAT_ID)
        )

        self.stale_product.refresh_from_db()
        self.assertFalse(self.stale_product.is_available)

    @override_settings(ENABLE_TELEGRAM_BOT_PROMPTS=False)
    def test_answers_apply_while_outbound_prompts_are_disabled(self):
        """The flag is outbound policy. A merchant who came to us must still work."""
        self.assertIsNotNone(
            handle_product_callback(f"pconfirm:{self.stale_product.pk}:0", CHAT_ID)
        )
        self.stale_product.refresh_from_db()
        self.assertIsNotNone(self.stale_product.last_confirmed_at)


class MyProductsHandlerTests(BotTestCase):
    """PTB wiring for /myproducts — listing, paging, and multi-answer flow."""

    def _message_update(self, chat_id=CHAT_ID):
        update = mock.Mock()
        update.message = mock.AsyncMock()
        update.effective_chat.id = int(chat_id)
        return update, update.message

    def _callback_update(self, data, chat_id=CHAT_ID):
        query = mock.AsyncMock()
        query.data = data
        query.message.chat.id = int(chat_id)
        update = mock.Mock()
        update.callback_query = query
        return update, query

    def _stale_extras(self, count):
        return [
            self._product(
                f"Extra {i}",
                f"extra-{i}",
                self.shop,
                timezone.now() - timedelta(days=40),
            )
            for i in range(count)
        ]

    def test_listing_offers_a_button_per_product(self):
        update, message = self._message_update()

        async_to_sync(my_products)(update, None)

        markup = message.reply_text.call_args.kwargs["reply_markup"]
        self.assertEqual(len(markup.inline_keyboard), 1)
        confirm, deny = markup.inline_keyboard[0]
        self.assertEqual(confirm.callback_data, f"pconfirm:{self.stale_product.pk}:0")
        self.assertEqual(deny.callback_data, f"pdeny:{self.stale_product.pk}:0")

    def test_an_unlinked_chat_is_told_how_to_link(self):
        update, message = self._message_update(OTHER_CHAT_ID)

        async_to_sync(my_products)(update, None)

        self.assertIn("/link", message.reply_text.call_args.args[0])

    def test_an_up_to_date_shop_is_told_there_is_nothing_to_do(self):
        Product.objects.filter(pk=self.stale_product.pk).update(
            last_confirmed_at=timezone.now()
        )
        update, message = self._message_update()

        async_to_sync(my_products)(update, None)

        self.assertIn("Nothing needs confirming", message.reply_text.call_args.args[0])

    def test_a_long_list_is_capped_and_paged(self):
        self._stale_extras(7)
        update, message = self._message_update()

        async_to_sync(my_products)(update, None)

        rows = message.reply_text.call_args.kwargs["reply_markup"].inline_keyboard
        # One row per product on this page, plus a navigation row.
        self.assertEqual(len(rows), settings.MYPRODUCTS_PAGE_SIZE + 1)
        self.assertEqual(rows[-1][0].callback_data, "page:1")

    def test_paging_edits_the_message_instead_of_sending_another(self):
        self._stale_extras(7)
        update, query = self._callback_update("page:1")

        async_to_sync(on_callback)(update, None)

        query.edit_message_text.assert_awaited()
        rows = query.edit_message_text.call_args.kwargs["reply_markup"].inline_keyboard
        # Last page: three products and a nav row offering only Prev.
        self.assertEqual(rows[-1][0].callback_data, "page:0")

    def test_several_products_confirmed_in_one_interaction(self):
        """The point of the feature: no waiting for separate pings per product."""
        targets = [self.stale_product, *self._stale_extras(2)]
        untouched_at = self.fresh_product.last_confirmed_at

        for product in targets:
            update, query = self._callback_update(f"pconfirm:{product.pk}:0")
            async_to_sync(on_callback)(update, None)

        for product in targets:
            product.refresh_from_db()
            self.assertTrue(product.is_available)
            self.assertEqual(product.confirmation_status, ConfirmationStatus.FRESH)
            self.assertGreater(
                product.last_confirmed_at, timezone.now() - timedelta(minutes=1)
            )

        self.fresh_product.refresh_from_db()
        self.assertEqual(self.fresh_product.last_confirmed_at, untouched_at)

    def test_the_last_answer_clears_the_listing(self):
        update, query = self._callback_update(f"pconfirm:{self.stale_product.pk}:0")

        async_to_sync(on_callback)(update, None)

        query.answer.assert_awaited_with("Recorded")
        self.assertIn("Nothing left", query.edit_message_text.call_args.args[0])

    def test_a_callback_from_another_chat_changes_nothing(self):
        before = self.stale_product.last_confirmed_at
        update, query = self._callback_update(
            f"pconfirm:{self.stale_product.pk}:0", chat_id=OTHER_CHAT_ID
        )

        async_to_sync(on_callback)(update, None)

        self.stale_product.refresh_from_db()
        self.assertEqual(self.stale_product.last_confirmed_at, before)
        query.edit_message_text.assert_not_awaited()

    @override_settings(ENABLE_TELEGRAM_BOT_PROMPTS=False)
    def test_the_listing_works_while_outbound_prompts_are_disabled(self):
        update, message = self._message_update()

        async_to_sync(my_products)(update, None)

        self.assertIn("reply_markup", message.reply_text.call_args.kwargs)


class TextReplyTests(BotTestCase):
    """Free-text answers — the path a merchant takes when they ignore the buttons."""

    def _request(self, product=None, message_id=None, status="pending"):
        return ConfirmationRequest.objects.create(
            product=product or self.stale_product,
            shop=self.shop,
            status=status,
            telegram_message_id=message_id,
        )

    def test_still_available_confirms_the_pending_request(self):
        request = self._request()

        outcome, _ = handle_text_reply("Still available", chat_id=CHAT_ID)

        self.assertEqual(outcome, "confirmed")
        request.refresh_from_db()
        self.stale_product.refresh_from_db()
        self.assertEqual(request.status, "confirmed")
        self.assertIsNotNone(request.responded_at)
        self.assertEqual(
            self.stale_product.confirmation_status, ConfirmationStatus.FRESH
        )
        self.assertTrue(self.stale_product.is_available)
        self.assertGreater(
            self.stale_product.last_confirmed_at, timezone.now() - timedelta(minutes=1)
        )

    def test_sold_out_marks_the_product_unavailable(self):
        request = self._request()

        outcome, _ = handle_text_reply("sold out", chat_id=CHAT_ID)

        self.assertEqual(outcome, "sold_out")
        request.refresh_from_db()
        self.stale_product.refresh_from_db()
        self.assertEqual(request.status, "sold_out")
        self.assertFalse(self.stale_product.is_available)
        # Out of stock is fresh information, not the absence of it.
        self.assertEqual(
            self.stale_product.confirmation_status, ConfirmationStatus.FRESH
        )

    def test_an_orphan_reply_changes_nothing(self):
        before = self.stale_product.last_confirmed_at

        outcome, product = handle_text_reply("yes", chat_id=CHAT_ID)

        self.assertEqual(outcome, "orphan")
        self.assertIsNone(product)
        self.stale_product.refresh_from_db()
        self.assertEqual(self.stale_product.last_confirmed_at, before)

    def test_two_pending_requests_are_ambiguous(self):
        self._request()
        self._request(product=self.fresh_product)

        self.assertEqual(handle_text_reply("yes", chat_id=CHAT_ID)[0], "ambiguous")
        self.assertEqual(
            ConfirmationRequest.objects.filter(status="pending").count(), 2
        )

    def test_replying_to_a_prompt_disambiguates(self):
        first = self._request(message_id="4242")
        second = self._request(product=self.fresh_product, message_id="4243")

        outcome, product = handle_text_reply(
            "yes", chat_id=CHAT_ID, reply_to_message_id=4243
        )

        self.assertEqual(outcome, "confirmed")
        self.assertEqual(product.pk, self.fresh_product.pk)
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(first.status, "pending")
        self.assertEqual(second.status, "confirmed")

    def test_a_reply_to_an_already_answered_prompt_is_inactive(self):
        self._request(message_id="4242", status="confirmed")

        outcome, _ = handle_text_reply("yes", chat_id=CHAT_ID, reply_to_message_id=4242)

        self.assertEqual(outcome, "inactive")

    def test_unrecognised_wording_is_not_guessed_at(self):
        """Whole-phrase matching: "no longer available" contains "available"."""
        request = self._request()

        for text in ("maybe later", "no longer available", "how much?"):
            self.assertEqual(handle_text_reply(text, chat_id=CHAT_ID)[0], "unparsed")

        request.refresh_from_db()
        self.assertEqual(request.status, "pending")

    def test_another_chat_cannot_answer_by_text(self):
        request = self._request()

        self.assertEqual(handle_text_reply("yes", chat_id=OTHER_CHAT_ID)[0], "orphan")

        request.refresh_from_db()
        self.assertEqual(request.status, "pending")

    @override_settings(ENABLE_TELEGRAM_BOT_PROMPTS=False)
    def test_answers_apply_while_outbound_prompts_are_disabled(self):
        """The flag is outbound policy. A merchant who typed at us must still work."""
        request = self._request()

        self.assertEqual(handle_text_reply("yes", chat_id=CHAT_ID)[0], "confirmed")

        request.refresh_from_db()
        self.assertEqual(request.status, "confirmed")


class TextHandlerTests(BotTestCase):
    """PTB wiring for text replies — the state change is TextReplyTests' job."""

    def _message_update(self, text, chat_id=CHAT_ID, reply_to_message_id=None):
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

    def test_the_handler_applies_an_answer_and_acknowledges(self):
        request = ConfirmationRequest.objects.create(
            product=self.stale_product, shop=self.shop, status="pending"
        )
        update, message = self._message_update("still available")

        async_to_sync(on_text)(update, None)

        request.refresh_from_db()
        self.assertEqual(request.status, "confirmed")
        self.assertIn("in stock", message.reply_text.call_args.args[0])

    def test_an_unmatched_reply_gets_a_way_forward(self):
        update, message = self._message_update("yes")

        async_to_sync(on_text)(update, None)

        self.assertIn("/myproducts", message.reply_text.call_args.args[0])


class SendPromptCommandTests(BotTestCase):
    """The hand-run trigger: one named product, and every refusal explained."""

    def _run(self, *args):
        out = StringIO()
        call_command("send_prompt", *args, stdout=out)
        return out.getvalue()

    def test_it_prompts_the_named_product(self):
        with mock.patch(
            "bot.telegram_api.send_message", return_value={"message_id": 77}
        ) as send:
            output = self._run(str(self.stale_product.pk))

        send.assert_called_once()
        self.assertIn("Sent to chat", output)
        self.assertEqual(
            ConfirmationRequest.objects.filter(
                product=self.stale_product, status="pending"
            ).count(),
            1,
        )

    def test_a_slug_identifies_the_product_too(self):
        with mock.patch(
            "bot.telegram_api.send_message", return_value={"message_id": 77}
        ) as send:
            self._run(self.stale_product.slug)

        self.assertIn("Galaxy S23", send.call_args.kwargs["text"])

    def test_it_shows_the_message_before_sending_anything(self):
        with mock.patch(
            "bot.telegram_api.send_message", return_value={"message_id": 77}
        ):
            output = self._run(str(self.stale_product.pk))

        self.assertIn("10,000.00 ETB", output)
        self.assertIn("reply YES or NO", output)

    @override_settings(TELEGRAM_BOT_TOKEN="")
    def test_without_a_token_it_still_prints_the_wording(self):
        output = self._run(str(self.stale_product.pk))

        self.assertIn("10,000.00 ETB", output)
        self.assertIn("nothing left the server", output)

    def test_a_product_with_a_pending_request_is_not_asked_twice(self):
        ConfirmationRequest.objects.create(
            product=self.stale_product, shop=self.shop, status="pending"
        )

        output = self._run(str(self.stale_product.pk))

        self.assertIn("already has a pending request", output)
        self.assertEqual(ConfirmationRequest.objects.count(), 1)

    @override_settings(ENABLE_TELEGRAM_BOT_PROMPTS=False)
    def test_the_policy_flag_blocks_it_by_default(self):
        output = self._run(str(self.stale_product.pk))

        self.assertIn("ENABLE_TELEGRAM_BOT_PROMPTS is False", output)
        self.assertEqual(ConfirmationRequest.objects.count(), 0)

    @override_settings(ENABLE_TELEGRAM_BOT_PROMPTS=False)
    def test_force_sends_while_the_flag_is_off(self):
        with mock.patch(
            "bot.telegram_api.send_message", return_value={"message_id": 77}
        ) as send:
            output = self._run(str(self.stale_product.pk), "--force")

        send.assert_called_once()
        self.assertIn("Sent to chat", output)

    @override_settings(ENABLE_TELEGRAM_BOT_PROMPTS=False)
    def test_force_does_not_lift_the_double_send_guard(self):
        """--force is a policy override, not a licence to pester a merchant."""
        ConfirmationRequest.objects.create(
            product=self.stale_product, shop=self.shop, status="pending"
        )

        output = self._run(str(self.stale_product.pk), "--force")

        self.assertIn("already has a pending request", output)
        self.assertEqual(ConfirmationRequest.objects.count(), 1)

    @override_settings(ENABLE_TELEGRAM_BOT_PROMPTS=False)
    def test_force_cannot_send_to_a_shop_with_no_chat_id(self):
        with self.assertRaisesMessage(CommandError, "no telegram_chat_id"):
            self._run(str(self.unconfirmed_product.pk), "--force")

    def test_an_unknown_product_is_an_error(self):
        with self.assertRaisesMessage(CommandError, "No product matching"):
            self._run("99999999")
        with self.assertRaisesMessage(CommandError, "No product matching"):
            self._run("no-such-slug")
