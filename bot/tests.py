import types
import zoneinfo
from datetime import datetime, timedelta
from io import StringIO
from pathlib import Path
from unittest import mock

from asgiref.sync import async_to_sync
from django.conf import settings
from django.contrib.admin.sites import site
from django.contrib.auth import get_user_model
from django.core.management import CommandError, call_command
from django.db import IntegrityError, transaction
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone
from telegram.ext import ApplicationHandlerStop

from bot import keyboards, messages, parsing
from bot import telegram_api as telegram
from bot.management.commands.run_bot import (
    dedupe,
    help,
    menu,
    my_products,
    on_callback,
    on_error,
    on_text,
    status,
)
from bot.models import (
    ConfirmationDigest,
    ConfirmationRequest,
    OutboundMessage,
    ProcessedUpdate,
)
from bot.selectors import listing_for_chat, shops_for_chat
from bot.sender import drain_outbox
from bot.services import (
    handle_product_callback,
    handle_telegram_callback,
    handle_text_reply,
    parse_answer,
    record_update,
    review_next,
    send_confirmation_prompt,
    shop_status,
    start_review,
)
from catalog.models import Category, ConfirmationStatus, Product, ProductStatus
from catalog.services import apply_confirmation
from shops.models import Shop

User = get_user_model()

CHAT_ID = "987654321"
OTHER_CHAT_ID = "111000111"


@override_settings(
    TELEGRAM_BOT_TOKEN="test-token",
    ENABLE_TELEGRAM_BOT_PROMPTS=True,
    TELEGRAM_MOCK_SEND=False,
)
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
    def test_prompt_enqueues_outbox_message_transactionally(self):
        request = send_confirmation_prompt(self.stale_product)
        self.assertIsNotNone(request)
        outbox_msg = OutboundMessage.objects.get(dedupe_key=f"prompt:{request.pk}")
        self.assertEqual(outbox_msg.chat_id, CHAT_ID)
        self.assertEqual(outbox_msg.kind, "prompt")
        self.assertEqual(outbox_msg.status, "queued")
        self.assertEqual(outbox_msg.payload["confirmation_request_id"], request.pk)
        self.assertIn(self.stale_product.name, outbox_msg.payload["text"])

    def test_payload_is_addressed_to_the_owning_shop(self):
        with mock.patch(
            "bot.telegram_api.send_message", return_value={"message_id": 4242}
        ) as send:
            request = send_confirmation_prompt(self.stale_product)
            drain_outbox(rate_limit=False)

        send.assert_called_once()
        kwargs = send.call_args.kwargs
        self.assertEqual(kwargs["chat_id"], CHAT_ID)
        self.assertIn(self.stale_product.name, kwargs["text"])
        buttons = kwargs["reply_markup"]["inline_keyboard"][0]
        self.assertEqual(
            [b["callback_data"] for b in buttons],
            [f"confirm_{request.pk}", f"deny_{request.pk}"],
        )
        outbox_msg = OutboundMessage.objects.get(dedupe_key=f"prompt:{request.pk}")
        self.assertEqual(outbox_msg.status, "sent")

    def test_prompt_names_the_product_and_its_price(self):
        with mock.patch(
            "bot.telegram_api.send_message", return_value={"message_id": 1}
        ) as send:
            send_confirmation_prompt(self.stale_product)
            drain_outbox(rate_limit=False)

        text = send.call_args.kwargs["text"]
        self.assertIn("Test Galaxy S23", text)
        self.assertIn("10,000.00 ETB", text)
        self.assertIn(self.shop.name, text)

    def test_prompt_offers_both_a_button_and_a_typed_answer(self):
        with mock.patch(
            "bot.telegram_api.send_message", return_value={"message_id": 1}
        ) as send:
            send_confirmation_prompt(self.stale_product)
            drain_outbox(rate_limit=False)

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
            drain_outbox(rate_limit=False)
        request.refresh_from_db()
        self.assertEqual(request.telegram_message_id, "4242")

    def test_failed_send_keeps_pending_request_and_queues_retry(self):
        """With an outbox, a failed network send leaves the pending request in
        place so the sender can retry, while the outbox row remains queued."""
        with mock.patch(
            "bot.telegram_api.send_message",
            side_effect=telegram.TelegramNetworkError("network down"),
        ):
            request = send_confirmation_prompt(self.stale_product)
            self.assertIsNotNone(request)
            drain_outbox(rate_limit=False)

        self.assertEqual(ConfirmationRequest.objects.count(), 1)
        outbox_msg = OutboundMessage.objects.get(dedupe_key=f"prompt:{request.pk}")
        self.assertEqual(outbox_msg.status, "queued")
        self.assertEqual(outbox_msg.attempts, 1)

    def test_shop_without_telegram_is_skipped(self):
        self.assertIsNone(send_confirmation_prompt(self.unconfirmed_product))
        self.assertEqual(ConfirmationRequest.objects.count(), 0)

    def test_duplicate_prompt_is_skipped(self):
        with mock.patch(
            "bot.telegram_api.send_message", return_value={"message_id": 1}
        ) as send:
            self.assertIsNotNone(send_confirmation_prompt(self.stale_product))
            self.assertIsNone(send_confirmation_prompt(self.stale_product))
            drain_outbox(rate_limit=False)
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
            drain_outbox(rate_limit=False)

        self.assertEqual(send.call_count, 1)

    def test_unavailable_product_gets_a_back_in_stock_prompt(self):
        Product.objects.filter(pk=self.stale_product.pk).update(is_available=False)
        self.stale_product.refresh_from_db()
        with mock.patch(
            "bot.telegram_api.send_message", return_value={"message_id": 1}
        ) as send:
            send_confirmation_prompt(self.stale_product)
            drain_outbox(rate_limit=False)
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
        self.assertEqual(
            handle_telegram_callback(f"confirm_{request.pk}", chat_id=CHAT_ID),
            "applied",
        )
        request.refresh_from_db()
        self.stale_product.refresh_from_db()
        self.assertEqual(request.status, "confirmed")
        self.assertEqual(self.stale_product.confirmation_status, "fresh")
        self.assertTrue(self.stale_product.is_available)

    def test_deny_marks_product_unavailable(self):
        request = self._request()
        self.assertEqual(
            handle_telegram_callback(f"deny_{request.pk}", chat_id=CHAT_ID),
            "applied",
        )
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
        self.assertEqual(
            handle_telegram_callback(f"confirm_{request.pk}", chat_id=OTHER_CHAT_ID),
            "not_found",
        )
        request.refresh_from_db()
        self.stale_product.refresh_from_db()
        self.assertEqual(request.status, "pending")
        self.assertEqual(self.stale_product.confirmation_status, before)

    def test_confirmed_request_cannot_be_replayed(self):
        request = self._request(status="confirmed")
        self.assertEqual(
            handle_telegram_callback(f"confirm_{request.pk}", chat_id=CHAT_ID),
            "already_answered",
        )

    def test_expired_request_applies_answer_and_leaves_request_expired(self):
        request = self._request(status="expired")
        before_last_confirmed = self.stale_product.last_confirmed_at
        outcome = handle_telegram_callback(f"confirm_{request.pk}", chat_id=CHAT_ID)
        self.assertEqual(outcome, "applied")

        request.refresh_from_db()
        self.assertEqual(request.status, "expired")
        self.assertIsNone(request.responded_at)

        self.stale_product.refresh_from_db()
        self.assertEqual(self.stale_product.confirmation_status, "fresh")
        self.assertTrue(self.stale_product.is_available)
        self.assertGreater(self.stale_product.last_confirmed_at, before_last_confirmed)

    def test_already_answered_request_returns_already_answered_and_does_no_write(self):
        request = self._request(status="confirmed")
        before_last_confirmed = self.stale_product.last_confirmed_at
        outcome = handle_telegram_callback(f"deny_{request.pk}", chat_id=CHAT_ID)
        self.assertEqual(outcome, "already_answered")

        request.refresh_from_db()
        self.assertEqual(request.status, "confirmed")

        self.stale_product.refresh_from_db()
        self.assertEqual(self.stale_product.last_confirmed_at, before_last_confirmed)

    def test_malformed_callback_is_rejected(self):
        self.assertEqual(
            handle_telegram_callback("nonsense", chat_id=CHAT_ID), "not_found"
        )
        self.assertEqual(
            handle_telegram_callback("confirm_abc", chat_id=CHAT_ID), "not_found"
        )

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
    def test_sweep_marks_stale_and_prompts_zero(self):
        out = StringIO()
        with mock.patch(
            "bot.telegram_api.send_message", return_value={"message_id": 1}
        ):
            call_command("sweep_freshness", "--staleness-days=30", stdout=out)
        output = out.getvalue()

        self.assertIn("[Freshness Sweep Complete]", output)
        self.assertIn("Stale products: 2", output)
        self.assertIn("Prompts sent: 0", output)

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
        self.assertFalse(
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
            ConfirmationRequest.objects.filter(product=self.stale_product).count(), 0
        )

    def test_expires_old_pending_requests(self):
        old = ConfirmationRequest.objects.create(
            product=self.stale_product,
            shop=self.shop,
            status="pending",
            expires_at=timezone.now() - timedelta(hours=1),
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
        ConfirmationRequest.objects.create(
            product=self.stale_product,
            shop=self.shop,
            status="pending",
            expires_at=timezone.now() - timedelta(hours=1),
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

    def test_an_unconfirmed_product_is_not_prompted_by_sweep(self):
        """The weekly digest asks; sweep only reconciles and sends zero prompts."""
        Product.objects.filter(pk=self.stale_product.pk).update(
            confirmation_status=ConfirmationStatus.UNCONFIRMED
        )
        out = StringIO()
        with mock.patch(
            "bot.telegram_api.send_message", return_value={"message_id": 1}
        ):
            call_command("sweep_freshness", stdout=out)

        self.assertIn("Prompts sent: 0", out.getvalue())
        self.stale_product.refresh_from_db()
        self.assertEqual(
            self.stale_product.confirmation_status, ConfirmationStatus.UNCONFIRMED
        )

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
        with override_settings(CONFIRMATION_INTERVAL_DAYS=90):
            with mock.patch(
                "bot.telegram_api.send_message", return_value={"message_id": 1}
            ):
                call_command("sweep_freshness", stdout=StringIO())
        self.stale_product.refresh_from_db()
        self.assertEqual(self.stale_product.confirmation_status, "fresh")

    def test_sweep_boundaries_6d_fresh_and_8d_stale(self):
        now = timezone.now()
        p_6d = self._product("p-6d", "p-6d", self.shop, now - timedelta(days=6))
        p_8d = self._product("p-8d", "p-8d", self.shop, now - timedelta(days=8))
        p_6d.confirmation_status = ConfirmationStatus.FRESH
        p_6d.save(update_fields=["confirmation_status"])
        p_8d.confirmation_status = ConfirmationStatus.FRESH
        p_8d.save(update_fields=["confirmation_status"])

        with mock.patch(
            "bot.telegram_api.send_message", return_value={"message_id": 1}
        ):
            call_command("sweep_freshness", stdout=StringIO())

        p_6d.refresh_from_db()
        p_8d.refresh_from_db()
        self.assertEqual(p_6d.confirmation_status, ConfirmationStatus.FRESH)
        self.assertEqual(p_8d.confirmation_status, ConfirmationStatus.STALE)

    def test_dry_run_mutates_nothing(self):
        out = StringIO()
        call_command("sweep_freshness", "--dry-run", stdout=out)
        self.assertIn("[DRY RUN]", out.getvalue())
        self.stale_product.refresh_from_db()
        self.assertEqual(self.stale_product.confirmation_status, "fresh")
        self.assertEqual(ConfirmationRequest.objects.count(), 0)

    @override_settings(ENABLE_TELEGRAM_BOT_PROMPTS=False)
    def test_dry_run_reports_no_prompts_when_sending_is_disabled(self):
        """The dry run must report zero prompts."""
        out = StringIO()
        call_command("sweep_freshness", "--dry-run", stdout=out)
        self.assertIn("Prompts sent: 0", out.getvalue())

    def test_dry_run_counts_an_expiry(self):
        """A dry run reports expired requests without mutating."""
        old = ConfirmationRequest.objects.create(
            product=self.stale_product,
            shop=self.shop,
            status="pending",
            expires_at=timezone.now() - timedelta(hours=1),
        )
        out = StringIO()
        call_command("sweep_freshness", "--dry-run", stdout=out)
        self.assertIn("Expired requests: 1", out.getvalue())
        self.assertIn("Prompts sent: 0", out.getvalue())
        old.refresh_from_db()
        self.assertEqual(old.status, "pending")


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


class ReviewModeTests(BotTestCase):
    def setUp(self):
        super().setUp()
        self.category = Category.objects.create(
            name="Review Phones", slug="review-phones"
        )
        self.p1 = Product.objects.create(
            shop=self.shop,
            category=self.category,
            brand="BrandA",
            name="Phone 1",
            price="100.00",
            is_available=True,
            status="live",
            last_confirmed_at=timezone.now() - timedelta(days=8),
        )
        self.p2 = Product.objects.create(
            shop=self.shop,
            category=self.category,
            brand="BrandB",
            name="Phone 2",
            price="200.00",
            is_available=True,
            status="live",
            last_confirmed_at=timezone.now() - timedelta(days=9),
        )
        self.p3 = Product.objects.create(
            shop=self.shop,
            category=self.category,
            brand="BrandC",
            name="Phone 3",
            price="300.00",
            is_available=True,
            status="live",
            last_confirmed_at=timezone.now() - timedelta(days=10),
        )
        self.digest = ConfirmationDigest.objects.create(
            shop=self.shop,
            for_date=timezone.now().date(),
        )
        self.req1 = ConfirmationRequest.objects.create(
            shop=self.shop,
            product=self.p1,
            digest=self.digest,
            status="pending",
        )
        self.req2 = ConfirmationRequest.objects.create(
            shop=self.shop,
            product=self.p2,
            digest=self.digest,
            status="pending",
        )
        self.req3 = ConfirmationRequest.objects.create(
            shop=self.shop,
            product=self.p3,
            digest=self.digest,
            status="pending",
        )

    def test_start_review_shows_first_pending_item(self):
        outcome, cursor, item, progress = start_review(CHAT_ID, self.digest.pk)
        self.assertEqual(outcome, "item")
        self.assertEqual(cursor, 0)
        self.assertEqual(item.request_id, self.req1.pk)
        self.assertEqual(progress.index, 1)
        self.assertEqual(progress.total, 3)

    def test_review_step_y_and_n_answer_and_advance(self):
        # Answer req1 as available ('y')
        outcome, cursor, item, progress = review_next(
            CHAT_ID, self.digest.pk, "y", self.req1.pk
        )
        self.assertEqual(outcome, "item")
        self.assertEqual(cursor, 1)
        self.assertEqual(item.request_id, self.req2.pk)
        self.assertEqual(progress.index, 2)
        self.assertEqual(progress.total, 3)

        self.req1.refresh_from_db()
        self.assertEqual(self.req1.status, "confirmed")
        self.p1.refresh_from_db()
        self.assertEqual(self.p1.confirmation_status, "fresh")
        self.assertTrue(self.p1.is_available)

        # Answer req2 as sold ('n')
        outcome, cursor, item, progress = review_next(
            CHAT_ID, self.digest.pk, "n", self.req2.pk
        )
        self.assertEqual(outcome, "item")
        self.assertEqual(cursor, 2)
        self.assertEqual(item.request_id, self.req3.pk)
        self.assertEqual(progress.index, 3)

        self.req2.refresh_from_db()
        self.assertEqual(self.req2.status, "sold_out")
        self.p2.refresh_from_db()
        self.assertFalse(self.p2.is_available)

    def test_review_skip_does_no_write_and_advances(self):
        outcome, cursor, item, progress = review_next(
            CHAT_ID, self.digest.pk, "s", self.req1.pk
        )
        self.assertEqual(outcome, "item")
        self.assertEqual(cursor, 1)
        self.assertEqual(item.request_id, self.req2.pk)

        self.req1.refresh_from_db()
        self.assertEqual(self.req1.status, "pending")

    def test_review_pause_leaves_digest_open(self):
        outcome, cursor, item, progress = review_next(
            CHAT_ID, self.digest.pk, "x", self.req1.pk
        )
        self.assertEqual(outcome, "paused")
        self.digest.refresh_from_db()
        self.assertIsNone(self.digest.closed_at)

    def test_completing_last_item_sets_closed_at_and_returns_done(self):
        review_next(CHAT_ID, self.digest.pk, "y", self.req1.pk)
        review_next(CHAT_ID, self.digest.pk, "y", self.req2.pk)
        outcome, cursor, item, progress = review_next(
            CHAT_ID, self.digest.pk, "n", self.req3.pk
        )
        self.assertEqual(outcome, "done")
        self.assertEqual(progress.confirmed, 2)
        self.assertEqual(progress.sold, 1)

        self.digest.refresh_from_db()
        self.assertIsNotNone(self.digest.closed_at)

    def test_second_tap_on_old_review_button_is_harmless(self):
        # First tap
        outcome1, _, _, _ = review_next(CHAT_ID, self.digest.pk, "y", self.req1.pk)
        self.assertEqual(outcome1, "item")
        # Second tap on same request
        outcome2, _, item2, _ = review_next(CHAT_ID, self.digest.pk, "y", self.req1.pk)
        self.assertEqual(outcome2, "item")
        self.assertEqual(item2.request_id, self.req2.pk)

    def test_foreign_chat_returns_not_found(self):
        outcome, _, _, _ = start_review(OTHER_CHAT_ID, self.digest.pk)
        self.assertEqual(outcome, "not_found")
        outcome, _, _, _ = review_next(OTHER_CHAT_ID, self.digest.pk, "y", self.req1.pk)
        self.assertEqual(outcome, "not_found")


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
        update, query = self._update("confirm_99999999")
        async_to_sync(on_callback)(update, None)

        query.answer.assert_awaited_with("This request is no longer active.")
        query.edit_message_text.assert_not_awaited()

    def test_handler_reports_an_already_answered_request(self):
        request = ConfirmationRequest.objects.create(
            product=self.stale_product, shop=self.shop, status="confirmed"
        )
        update, query = self._update(f"confirm_{request.pk}")
        async_to_sync(on_callback)(update, None)

        query.answer.assert_awaited_with("Response recorded")
        query.edit_message_text.assert_awaited_with("Already recorded ✓")

    def test_handler_rejects_a_callback_from_another_chat(self):
        request = ConfirmationRequest.objects.create(
            product=self.stale_product, shop=self.shop, status="pending"
        )
        update, query = self._update(f"confirm_{request.pk}", chat_id=OTHER_CHAT_ID)
        async_to_sync(on_callback)(update, None)

        request.refresh_from_db()
        self.assertEqual(request.status, "pending")

    def test_handler_answers_digest_all_available(self):
        today = timezone.localtime(timezone.now()).date()
        digest = ConfirmationDigest.objects.create(shop=self.shop, for_date=today)
        request = ConfirmationRequest.objects.create(
            product=self.stale_product, shop=self.shop, digest=digest, status="pending"
        )
        update, query = self._update(f"da:{digest.pk}")
        async_to_sync(on_callback)(update, None)

        request.refresh_from_db()
        digest.refresh_from_db()
        self.assertEqual(request.status, "confirmed")
        self.assertIsNotNone(digest.closed_at)
        query.answer.assert_awaited_with(messages.response_recorded())
        query.edit_message_text.assert_awaited_with(messages.digest_ack(1))

    def test_handler_answers_digest_already_closed(self):
        today = timezone.localtime(timezone.now()).date()
        digest = ConfirmationDigest.objects.create(
            shop=self.shop, for_date=today, closed_at=timezone.now()
        )
        update, query = self._update(f"da:{digest.pk}")
        async_to_sync(on_callback)(update, None)

        query.answer.assert_awaited_with(messages.response_recorded())
        query.edit_message_text.assert_awaited_with(messages.digest_already_done())

    def test_handler_digest_rejects_foreign_chat(self):
        today = timezone.localtime(timezone.now()).date()
        digest = ConfirmationDigest.objects.create(shop=self.shop, for_date=today)
        request = ConfirmationRequest.objects.create(
            product=self.stale_product, shop=self.shop, digest=digest, status="pending"
        )
        update, query = self._update(f"da:{digest.pk}", chat_id=OTHER_CHAT_ID)
        async_to_sync(on_callback)(update, None)

        request.refresh_from_db()
        self.assertEqual(request.status, "pending")
        query.answer.assert_awaited_with(messages.request_inactive())
        query.edit_message_text.assert_not_awaited()

    def test_handler_starts_review_mode(self):
        today = timezone.localtime(timezone.now()).date()
        digest = ConfirmationDigest.objects.create(shop=self.shop, for_date=today)
        ConfirmationRequest.objects.create(
            product=self.stale_product, shop=self.shop, digest=digest, status="pending"
        )
        update, query = self._update(f"drev:{digest.pk}")
        async_to_sync(on_callback)(update, None)

        query.answer.assert_awaited()
        query.edit_message_text.assert_awaited()
        call_args = query.edit_message_text.await_args
        self.assertIn("Checking 1 of 1", call_args[0][0])

    def test_handler_processes_review_action(self):
        today = timezone.localtime(timezone.now()).date()
        digest = ConfirmationDigest.objects.create(shop=self.shop, for_date=today)
        req = ConfirmationRequest.objects.create(
            product=self.stale_product, shop=self.shop, digest=digest, status="pending"
        )
        update, query = self._update(f"cr:{req.pk}:y")
        async_to_sync(on_callback)(update, None)

        query.answer.assert_awaited_with("Response recorded")
        query.edit_message_text.assert_awaited()
        call_args = query.edit_message_text.await_args
        self.assertIn("Review complete!", call_args[0][0])
        digest.refresh_from_db()
        self.assertIsNotNone(digest.closed_at)

    def test_handler_pauses_review_mode(self):
        today = timezone.localtime(timezone.now()).date()
        digest = ConfirmationDigest.objects.create(shop=self.shop, for_date=today)
        req = ConfirmationRequest.objects.create(
            product=self.stale_product, shop=self.shop, digest=digest, status="pending"
        )
        update, query = self._update(f"cr:{req.pk}:x")
        async_to_sync(on_callback)(update, None)

        query.answer.assert_awaited()
        query.edit_message_text.assert_awaited_with(messages.review_paused())
        digest.refresh_from_db()
        self.assertIsNone(digest.closed_at)


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

    def test_fresh_and_draft_products_are_excluded(self):
        Product.objects.filter(pk=self.stale_product.pk).update(
            status=ProductStatus.DRAFT
        )

        _, products = listing_for_chat(CHAT_ID)

        self.assertEqual(products, [])

    def test_out_of_stock_products_included_in_restockable_section(self):
        Product.objects.filter(pk=self.stale_product.pk).update(
            is_available=False, status=ProductStatus.SOLD
        )

        _, products = listing_for_chat(CHAT_ID)

        self.assertEqual([p.pk for p in products], [self.stale_product.pk])

    def test_due_products_precede_restockable_products(self):
        sold = self._product("Pixel 8", "pixel-8", self.shop, None)
        Product.objects.filter(pk=sold.pk).update(
            is_available=False, status=ProductStatus.SOLD
        )

        _, products = listing_for_chat(CHAT_ID)

        # self.stale_product is due (available=True); sold is restockable (available=False).
        # Due items must lead so they never get crowded out of page 1.
        self.assertEqual([p.pk for p in products], [self.stale_product.pk, sold.pk])

    def test_restockable_products_capped_at_limit(self):
        with override_settings(MYPRODUCTS_RESTOCKABLE_LIMIT=2):
            extras = [
                self._product(
                    f"Extra {i}",
                    f"extra-{i}",
                    self.shop,
                    timezone.now() - timedelta(days=i + 10),
                )
                for i in range(4)
            ]
            Product.objects.filter(pk__in=[p.pk for p in extras]).update(
                is_available=False, status=ProductStatus.SOLD
            )

            _, products = listing_for_chat(CHAT_ID)

            # 1 due (self.stale_product) + 2 restockable extras
            self.assertEqual(len(products), 3)
            self.assertEqual(products[0].pk, self.stale_product.pk)
            restockable_ids = [p.pk for p in products[1:]]
            self.assertEqual(len(restockable_ids), 2)
            self.assertEqual(restockable_ids, [extras[0].pk, extras[1].pk])

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

    def test_back_in_stock_republishes_out_of_stock_product(self):
        Product.objects.filter(pk=self.stale_product.pk).update(
            is_available=False, status=ProductStatus.SOLD
        )

        res = handle_product_callback(f"pback:{self.stale_product.pk}:0", CHAT_ID)
        self.assertIsNotNone(res)

        self.stale_product.refresh_from_db()
        self.assertTrue(self.stale_product.is_available)
        self.assertEqual(self.stale_product.status, ProductStatus.LIVE)
        self.assertEqual(
            self.stale_product.confirmation_status, ConfirmationStatus.FRESH
        )
        self.assertGreater(
            self.stale_product.last_confirmed_at,
            timezone.now() - timedelta(minutes=1),
        )

    def test_back_in_stock_on_available_product_is_noop(self):
        self.assertTrue(self.stale_product.is_available)
        before = self.stale_product.last_confirmed_at

        res = handle_product_callback(f"pback:{self.stale_product.pk}:0", CHAT_ID)
        self.assertIsNotNone(res)

        self.stale_product.refresh_from_db()
        self.assertTrue(self.stale_product.is_available)
        self.assertEqual(self.stale_product.last_confirmed_at, before)

    def test_back_in_stock_foreign_chat_rejected(self):
        Product.objects.filter(pk=self.stale_product.pk).update(is_available=False)

        self.assertIsNone(
            handle_product_callback(f"pback:{self.stale_product.pk}:0", OTHER_CHAT_ID)
        )

        self.stale_product.refresh_from_db()
        self.assertFalse(self.stale_product.is_available)


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
        # One row per product, then the ☰ Menu row.
        self.assertEqual(len(markup.inline_keyboard), 2)
        confirm, deny = markup.inline_keyboard[0]
        self.assertEqual(confirm.callback_data, f"pconfirm:{self.stale_product.pk}:0")
        self.assertEqual(deny.callback_data, f"pdeny:{self.stale_product.pk}:0")
        self.assertEqual(markup.inline_keyboard[-1][0].callback_data, "menu:home")

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
        self._stale_extras(settings.MYPRODUCTS_PAGE_SIZE + 2)
        update, message = self._message_update()

        async_to_sync(my_products)(update, None)

        rows = message.reply_text.call_args.kwargs["reply_markup"].inline_keyboard
        # One row per product on this page, a navigation row, and the ☰ Menu row.
        self.assertEqual(len(rows), settings.MYPRODUCTS_PAGE_SIZE + 2)
        self.assertEqual(rows[-2][0].callback_data, "page:1")
        self.assertEqual(rows[-1][0].callback_data, "menu:home")

    def test_paging_edits_the_message_instead_of_sending_another(self):
        self._stale_extras(settings.MYPRODUCTS_PAGE_SIZE + 2)
        update, query = self._callback_update("page:1")

        async_to_sync(on_callback)(update, None)

        query.edit_message_text.assert_awaited()
        rows = query.edit_message_text.call_args.kwargs["reply_markup"].inline_keyboard
        # Last page: products, a nav row offering only Prev, and the ☰ Menu row.
        self.assertEqual(rows[-2][0].callback_data, "page:0")

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

    def test_listing_shows_restock_button_for_unavailable_product(self):
        Product.objects.filter(pk=self.stale_product.pk).update(
            is_available=False, status=ProductStatus.SOLD
        )
        update, message = self._message_update()

        async_to_sync(my_products)(update, None)

        text = message.reply_text.call_args.args[0]
        self.assertIn("🔄 to put back in stock", text)
        markup = message.reply_text.call_args.kwargs["reply_markup"]
        # Restock row, then the ☰ Menu row.
        self.assertEqual(len(markup.inline_keyboard), 2)
        row = markup.inline_keyboard[0]
        self.assertEqual(len(row), 1)
        button = row[0]
        self.assertTrue(button.text.startswith("🔄"))
        self.assertEqual(button.callback_data, f"pback:{self.stale_product.pk}:0")

    def test_restock_button_restocks_and_redraws_listing(self):
        Product.objects.filter(pk=self.stale_product.pk).update(
            is_available=False, status=ProductStatus.SOLD
        )
        update, query = self._callback_update(f"pback:{self.stale_product.pk}:0")

        async_to_sync(on_callback)(update, None)

        query.answer.assert_awaited_with("Recorded")
        self.stale_product.refresh_from_db()
        self.assertTrue(self.stale_product.is_available)
        self.assertEqual(self.stale_product.status, ProductStatus.LIVE)
        query.edit_message_text.assert_awaited()


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

    def test_it_queues_a_prompt_for_the_named_product(self):
        output = self._run(str(self.stale_product.pk))

        self.assertIn("Queued for chat", output)
        request = ConfirmationRequest.objects.get(
            product=self.stale_product, status="pending"
        )
        outbox_msg = OutboundMessage.objects.get(dedupe_key=f"prompt:{request.pk}")
        self.assertEqual(outbox_msg.status, "queued")

    def test_a_slug_identifies_the_product_too(self):
        output = self._run(self.stale_product.slug)

        self.assertIn("Galaxy S23", output)

    def test_it_shows_the_message_before_queueing_anything(self):
        output = self._run(str(self.stale_product.pk))

        self.assertIn("10,000.00 ETB", output)
        self.assertIn("reply YES or NO", output)

    @override_settings(TELEGRAM_BOT_TOKEN="")
    def test_without_a_token_it_still_prints_the_wording(self):
        output = self._run(str(self.stale_product.pk))

        self.assertIn("10,000.00 ETB", output)
        self.assertIn("marked dead", output)

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
        output = self._run(str(self.stale_product.pk), "--force")

        self.assertIn("Queued for chat", output)

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


class RecordUpdateTests(TestCase):
    """Idempotency service tests for recording update IDs."""

    def test_fresh_update_records_and_returns_true(self):
        self.assertTrue(record_update(12345))
        self.assertTrue(ProcessedUpdate.objects.filter(update_id=12345).exists())

    def test_duplicate_update_returns_false(self):
        self.assertTrue(record_update(12345))
        self.assertFalse(record_update(12345))
        self.assertEqual(ProcessedUpdate.objects.filter(update_id=12345).count(), 1)

    def test_multiple_distinct_updates_record_independently(self):
        self.assertTrue(record_update(1001))
        self.assertTrue(record_update(1002))
        self.assertEqual(
            ProcessedUpdate.objects.filter(update_id__in=[1001, 1002]).count(), 2
        )


class DedupeHandlerTests(TestCase):
    """PTB TypeHandler dedupe logic: drop redelivered updates before handlers run."""

    def test_fresh_update_passes_through(self):
        update = mock.Mock()
        update.update_id = 98765432101
        context = mock.Mock()

        async_to_sync(dedupe)(update, context)
        self.assertTrue(ProcessedUpdate.objects.filter(update_id=98765432101).exists())

    def test_duplicate_update_raises_application_handler_stop(self):
        ProcessedUpdate.objects.create(update_id=98765432102)
        update = mock.Mock()
        update.update_id = 98765432102
        context = mock.Mock()

        with self.assertRaises(ApplicationHandlerStop):
            async_to_sync(dedupe)(update, context)

    def test_update_without_id_passes_through(self):
        context = mock.Mock()

        update_none = mock.Mock()
        update_none.update_id = None
        async_to_sync(dedupe)(update_none, context)

        update_no_attr = mock.Mock(spec=[])
        async_to_sync(dedupe)(update_no_attr, context)

    def test_inbound_update_clears_blocked_at_for_chat(self):
        user = User.objects.create_user(username="blocked_owner", password="pw")
        shop = Shop.objects.create(
            name="Blocked Shop",
            owner=user,
            telegram_chat_id="12345",
            blocked_at=timezone.now(),
        )
        update = mock.Mock()
        update.update_id = 98765432103
        update.effective_chat = mock.Mock(id=12345)
        context = mock.Mock()

        async_to_sync(dedupe)(update, context)

        shop.refresh_from_db()
        self.assertIsNone(shop.blocked_at)

    def test_duplicate_update_does_not_clear_blocked_at(self):
        user = User.objects.create_user(username="blocked_owner_2", password="pw")
        shop = Shop.objects.create(
            name="Blocked Shop 2",
            owner=user,
            telegram_chat_id="12345",
            blocked_at=timezone.now(),
        )
        ProcessedUpdate.objects.create(update_id=98765432104)
        update = mock.Mock()
        update.update_id = 98765432104
        update.effective_chat = mock.Mock(id=12345)
        context = mock.Mock()

        with self.assertRaises(ApplicationHandlerStop):
            async_to_sync(dedupe)(update, context)

        shop.refresh_from_db()
        self.assertIsNotNone(shop.blocked_at)

    def test_inbound_update_for_unblocked_chat_is_noop(self):
        user = User.objects.create_user(username="normal_owner", password="pw")
        shop = Shop.objects.create(
            name="Normal Shop",
            owner=user,
            telegram_chat_id="54321",
            blocked_at=None,
        )
        update = mock.Mock()
        update.update_id = 98765432105
        update.effective_chat = mock.Mock(id=54321)
        context = mock.Mock()

        async_to_sync(dedupe)(update, context)

        shop.refresh_from_db()
        self.assertIsNone(shop.blocked_at)


class GlobalErrorHandlerTests(TestCase):
    """PTB on_error global error handler."""

    def test_logs_error_and_replies_when_effective_message_exists(self):
        update = mock.Mock()
        update.update_id = 55555
        update.effective_chat = mock.Mock(id=67890)
        update.effective_message = mock.AsyncMock()
        context = mock.Mock()
        context.error = ValueError("Database connection lost")

        with self.assertLogs("bot.management.commands.run_bot", level="ERROR") as cm:
            async_to_sync(on_error)(update, context)

        update.effective_message.reply_text.assert_awaited_once_with(
            "Something went wrong, please try again."
        )
        self.assertTrue(
            any(
                "Exception while handling update 55555 (chat 67890): ValueError: Database connection lost"
                in log
                for log in cm.output
            )
        )

    def test_handles_update_none_without_exception(self):
        context = mock.Mock()
        context.error = RuntimeError("Polling transport timeout")

        with self.assertLogs("bot.management.commands.run_bot", level="ERROR") as cm:
            async_to_sync(on_error)(None, context)

        self.assertTrue(
            any(
                "Exception while handling update None (chat None): RuntimeError: Polling transport timeout"
                in log
                for log in cm.output
            )
        )

    def test_handles_update_without_effective_message(self):
        update = mock.Mock()
        update.update_id = 55556
        update.effective_chat = mock.Mock(id=67891)
        update.effective_message = None
        context = mock.Mock()
        context.error = RuntimeError("Unhandled update type")

        with self.assertLogs("bot.management.commands.run_bot", level="ERROR") as cm:
            async_to_sync(on_error)(update, context)

        self.assertTrue(
            any(
                "Exception while handling update 55556 (chat 67891): RuntimeError: Unhandled update type"
                in log
                for log in cm.output
            )
        )

    def test_catches_reply_failure_without_crashing_poller(self):
        update = mock.Mock()
        update.update_id = 55557
        update.effective_chat = mock.Mock(id=67892)
        update.effective_message = mock.AsyncMock()
        update.effective_message.reply_text.side_effect = Exception(
            "Network unreachable"
        )
        context = mock.Mock()
        context.error = ValueError("Original error")

        with self.assertLogs("bot.management.commands.run_bot", level="WARNING") as cm:
            async_to_sync(on_error)(update, context)

        self.assertTrue(
            any(
                "Failed to send error reply to chat 67892: Network unreachable" in log
                for log in cm.output
            )
        )


class NoOrmInHandlersTests(SimpleTestCase):
    """CI guard: handlers must never touch the ORM directly.

    DB access belongs strictly in application/domain services invoked via
    sync_to_async.
    """

    def _find_handler_files(self):
        base_dir = Path(settings.BASE_DIR)
        files = [base_dir / "bot" / "management" / "commands" / "run_bot.py"]
        handlers_dir = base_dir / "bot" / "handlers"
        if handlers_dir.is_dir():
            files.extend(handlers_dir.rglob("*.py"))
        return [f for f in files if f.exists()]

    def test_handler_files_contain_no_orm_objects_access(self):
        handler_files = self._find_handler_files()
        self.assertTrue(len(handler_files) > 0, "No handler files found to scan")

        violations = []
        for file_path in handler_files:
            content = file_path.read_text(encoding="utf-8")
            if ".objects" in content:
                for line_no, line in enumerate(content.splitlines(), start=1):
                    if ".objects" in line:
                        violations.append(f"{file_path.name}:{line_no}: {line.strip()}")

        self.assertEqual(
            violations,
            [],
            "Found direct ORM access (.objects) in handler files:\n"
            + "\n".join(violations),
        )

    def test_scanner_detects_objects_access_in_snippet(self):
        sample = "products = Product.objects.filter(active=True)"
        self.assertIn(".objects", sample)


class ProcessedUpdateAdminTests(TestCase):
    """Admin tests for ProcessedUpdate read-only registration."""

    def setUp(self):
        self.admin = site._registry[ProcessedUpdate]
        self.request = mock.Mock()

    def test_admin_is_read_only(self):
        self.assertFalse(self.admin.has_add_permission(self.request))
        self.assertFalse(self.admin.has_change_permission(self.request))

    def test_admin_list_display_and_filters(self):
        self.assertEqual(self.admin.list_display, ["update_id", "created_at"])
        self.assertEqual(self.admin.list_filter, ["created_at"])
        self.assertEqual(self.admin.search_fields, ["update_id"])


class MessagesTests(SimpleTestCase):
    """Pure unit tests for user-facing string generation."""

    def test_text_reply_ack_confirmed_matches_exact_pinned_wording(self):
        result = messages.text_reply_ack("confirmed", "Test Phone")
        self.assertEqual(result, "✅ Thanks — 'Test Phone' is marked in stock.")

    def test_text_reply_ack_all_branches(self):
        self.assertEqual(
            messages.text_reply_ack("sold_out", "Item"),
            "❌ Got it — 'Item' is marked out of stock.",
        )
        self.assertIn("Several products", messages.text_reply_ack("ambiguous"))
        self.assertIn("no longer active", messages.text_reply_ack("inactive"))
        self.assertIn("Nothing is waiting", messages.text_reply_ack("orphan"))
        self.assertIn("I didn't catch that", messages.text_reply_ack("unknown"))

    def test_prompt_formatting_available_and_unavailable(self):
        prompt_avail = messages.prompt(
            brand="Samsung",
            name="Galaxy",
            price="10000",
            currency="ETB",
            shop_name="Bole Shop",
            is_available=True,
        )
        self.assertIn("Is this still available?", prompt_avail)
        self.assertIn("Samsung Galaxy", prompt_avail)
        self.assertIn("10,000.00 ETB · Bole Shop", prompt_avail)
        self.assertIn("reply YES or NO", prompt_avail)

        prompt_unavail = messages.prompt(
            brand="Samsung",
            name="Galaxy",
            price=500.5,
            currency="ETB",
            shop_name="Bole Shop",
            is_available=False,
        )
        self.assertIn("Is this back in stock?", prompt_unavail)
        self.assertIn("500.50 ETB · Bole Shop", prompt_unavail)

    def test_welcome_and_help(self):
        welcome = messages.welcome(12345)
        self.assertIn("Welcome to Tinsae", welcome)
        self.assertIn("12345", welcome)
        self.assertIn("/myproducts", messages.help_text())

    def test_help_text_content_and_plain_text(self):
        help_msg = messages.help_text()
        self.assertIn("/myproducts", help_msg)
        self.assertIn("/status", help_msg)
        self.assertIn("/unlink", help_msg)
        self.assertIn("/help", help_msg)
        self.assertIn('"all"', help_msg)
        self.assertIn('"sold 2 5"', help_msg)
        # Ensure no html/markdown tags/markers
        self.assertNotIn("<", help_msg)
        self.assertNotIn(">", help_msg)
        self.assertNotIn("`", help_msg)
        self.assertNotIn("*", help_msg)

    def test_format_last_checkin_none_returns_never(self):
        self.assertEqual(messages.format_last_checkin(None), "never")

    def test_format_last_checkin_today(self):
        eat = zoneinfo.ZoneInfo("Africa/Addis_Ababa")
        now = datetime(2026, 9, 30, 14, 0, tzinfo=eat)
        dt = datetime(2026, 9, 30, 8, 14, tzinfo=eat)
        self.assertEqual(messages.format_last_checkin(dt, now=now), "today 08:14")

    def test_format_last_checkin_earlier_date(self):
        eat = zoneinfo.ZoneInfo("Africa/Addis_Ababa")
        now = datetime(2026, 9, 30, 14, 0, tzinfo=eat)
        dt = datetime(2026, 9, 3, 8, 14, tzinfo=eat)
        self.assertEqual(messages.format_last_checkin(dt, now=now), "3 Sep 08:14")

    def test_format_last_checkin_utc_conversion(self):
        # 05:14 UTC on Sep 30 is 08:14 EAT on Sep 30
        utc = zoneinfo.ZoneInfo("UTC")
        now = datetime(2026, 9, 30, 10, 0, tzinfo=utc)
        dt = datetime(2026, 9, 30, 5, 14, tzinfo=utc)
        self.assertEqual(messages.format_last_checkin(dt, now=now), "today 08:14")

    def test_status_formatting_single_shop(self):
        msg = messages.status(
            shop_name="Bole Shop",
            fresh=3,
            due=1,
            hidden=0,
            last_checkin_str="today 08:14",
            prefix_shop=False,
        )
        self.assertEqual(
            msg, "3 fresh, 1 due soon, 0 hidden · Last check-in: today 08:14"
        )

    def test_status_formatting_prefixed_shop(self):
        msg = messages.status(
            shop_name="Bole Shop",
            fresh=3,
            due=1,
            hidden=0,
            last_checkin_str="today 08:14",
            prefix_shop=True,
        )
        self.assertEqual(
            msg, "Bole Shop: 3 fresh, 1 due soon, 0 hidden · Last check-in: today 08:14"
        )

    def test_status_summary_formatting(self):
        # Empty
        self.assertEqual(messages.status_summary([]), messages.not_linked())
        # Single shop: no prefix
        single = messages.status_summary([("Bole Shop", 2, 0, 1, "today 08:14")])
        self.assertEqual(
            single, "2 fresh, 0 due soon, 1 hidden · Last check-in: today 08:14"
        )
        # Multiple shops: prefixed
        multi = messages.status_summary(
            [
                ("Bole Shop", 2, 0, 1, "today 08:14"),
                ("Piassa Shop", 0, 0, 0, "never"),
            ]
        )
        lines = multi.split("\n")
        self.assertEqual(len(lines), 2)
        self.assertEqual(
            lines[0],
            "Bole Shop: 2 fresh, 0 due soon, 1 hidden · Last check-in: today 08:14",
        )
        self.assertEqual(
            lines[1],
            "Piassa Shop: 0 fresh, 0 due soon, 0 hidden · Last check-in: never",
        )

    def test_link_ack_branches(self):
        self.assertIn("Bole Shop", messages.link_ack("linked", "Bole Shop"))
        self.assertIn("Bole Shop", messages.link_ack("relinked", "Bole Shop"))
        self.assertIn("Bole Shop", messages.link_ack("already_linked", "Bole Shop"))
        self.assertIn("expired", messages.link_ack("expired"))
        self.assertIn("already been used", messages.link_ack("used"))
        self.assertIn("don't recognise", messages.link_ack("invalid"))

    def test_digest_formatting(self):
        text = messages.digest(
            "Bole Electronics",
            [
                (1, "Apple iPhone 15", "10,000.00 ETB"),
                (2, "Samsung Galaxy", "8,000.00 ETB"),
            ],
            more_count=3,
            hidden_after_days=14,
        )
        self.assertIn("Weekly stock check for Bole Electronics:", text)
        self.assertIn("1. Apple iPhone 15 — 10,000.00 ETB", text)
        self.assertIn("2. Samsung Galaxy — 8,000.00 ETB", text)
        self.assertIn("…and 3 more", text)
        self.assertIn("hidden after 14 days", text)

    def test_digest_acks(self):
        self.assertEqual(
            messages.digest_ack(1), "✅ Thanks! 1 product is confirmed in stock."
        )
        self.assertEqual(
            messages.digest_ack(4), "✅ Thanks! All 4 products are confirmed in stock."
        )
        self.assertEqual(
            messages.digest_already_done(), "This digest has already been answered. 🎉"
        )

    def test_already_recorded(self):
        self.assertEqual(messages.already_recorded(), "Already recorded ✓")

    def test_no_longer_listed(self):
        self.assertEqual(
            messages.no_longer_listed(), "That product is no longer listed."
        )

    def test_review_item(self):
        msg = messages.review_item(1, 3, "Apple iPhone", "10,000.00 ETB", 8)
        self.assertIn("Checking 1 of 3:", msg)
        self.assertIn("Apple iPhone", msg)
        self.assertIn("10,000.00 ETB · last confirmed 8 days ago", msg)
        self.assertIn("Is this still available?", msg)

    def test_review_done(self):
        msg = messages.review_done(2, 1)
        self.assertIn("Review complete! 🎉", msg)
        self.assertIn("2 products marked in stock.", msg)
        self.assertIn("1 product marked out of stock.", msg)

    def test_review_paused(self):
        self.assertIn("Review paused", messages.review_paused())


class ParsingTests(SimpleTestCase):
    """Pure unit tests for answer parsing and callback data formatters/parsers."""

    def test_parse_answer_affirmative(self):
        for word in (
            "yes",
            "y",
            "yeah",
            "yep",
            "yup",
            "ok",
            "okay",
            "sure",
            "available",
            "still available",
            "in stock",
            "still in stock",
            "have",
            "still have",
            "we have it",
            "አለ",
        ):
            self.assertTrue(parsing.parse_answer(word), f"Expected True for {word!r}")

    def test_parse_answer_negative(self):
        for word in (
            "no",
            "n",
            "nope",
            "sold",
            "sold out",
            "soldout",
            "not available",
            "out of stock",
            "finished",
            "gone",
            "የለም",
        ):
            self.assertFalse(parsing.parse_answer(word), f"Expected False for {word!r}")

    def test_parse_answer_unrecognised_and_empty(self):
        self.assertIsNone(parsing.parse_answer(""))
        self.assertIsNone(parsing.parse_answer(None))
        self.assertIsNone(parsing.parse_answer("maybe next week"))
        self.assertIsNone(parsing.parse_answer("how much does it cost?"))

    def test_parse_answer_normalises_whitespace_case_and_punctuation(self):
        self.assertTrue(parsing.parse_answer("  YES!  "))
        self.assertTrue(parsing.parse_answer("Still Available..."))
        self.assertFalse(parsing.parse_answer("Sold out!"))

    def test_prompt_callback_round_trip(self):
        confirm_data = parsing.format_prompt_callback("confirm", 42)
        self.assertEqual(confirm_data, "confirm_42")
        self.assertEqual(parsing.parse_prompt_callback(confirm_data), ("confirm", 42))

        deny_data = parsing.format_prompt_callback("deny", 999)
        self.assertEqual(deny_data, "deny_999")
        self.assertEqual(parsing.parse_prompt_callback(deny_data), ("deny", 999))

    def test_prompt_callback_invalid(self):
        self.assertIsNone(parsing.parse_prompt_callback("confirm_abc"))
        self.assertIsNone(parsing.parse_prompt_callback("other_42"))
        self.assertIsNone(parsing.parse_prompt_callback("confirm"))
        self.assertIsNone(parsing.parse_prompt_callback(""))

    def test_listing_callback_round_trip(self):
        pconfirm_data = parsing.format_listing_callback("pconfirm", 101, 3)
        self.assertEqual(pconfirm_data, "pconfirm:101:3")
        self.assertEqual(
            parsing.parse_listing_callback(pconfirm_data),
            ("pconfirm", 101, 3),
        )

        pdeny_data = parsing.format_listing_callback("pdeny", 202, 0)
        self.assertEqual(pdeny_data, "pdeny:202:0")
        self.assertEqual(
            parsing.parse_listing_callback(pdeny_data),
            ("pdeny", 202, 0),
        )

        pback_data = parsing.format_restock_callback(303, 1)
        self.assertEqual(pback_data, "pback:303:1")
        self.assertEqual(
            parsing.parse_restock_callback(pback_data),
            (303, 1),
        )
        self.assertEqual(
            parsing.parse_listing_callback(pback_data),
            ("pback", 303, 1),
        )

    def test_listing_callback_invalid(self):
        self.assertIsNone(parsing.parse_listing_callback("pconfirm:abc:0"))
        self.assertIsNone(parsing.parse_listing_callback("unknown:1:0"))
        self.assertIsNone(parsing.parse_listing_callback("nonsense"))
        self.assertIsNone(parsing.parse_listing_callback(""))

    def test_page_callback_round_trip(self):
        page_data = parsing.format_page_callback(4)
        self.assertEqual(page_data, "page:4")
        self.assertEqual(parsing.parse_page_callback(page_data), 4)

    def test_page_callback_invalid(self):
        self.assertIsNone(parsing.parse_page_callback("page:abc"))
        self.assertIsNone(parsing.parse_page_callback("page:"))
        self.assertIsNone(parsing.parse_page_callback("other:1"))

    def test_unlink_callback_round_trip(self):
        unlink_yes = parsing.format_unlink_callback("yes")
        self.assertEqual(unlink_yes, "unlink:yes")
        self.assertEqual(parsing.parse_unlink_callback(unlink_yes), "yes")

        unlink_cancel = parsing.format_unlink_callback("cancel")
        self.assertEqual(unlink_cancel, "unlink:cancel")
        self.assertEqual(parsing.parse_unlink_callback(unlink_cancel), "cancel")

    def test_unlink_callback_invalid(self):
        self.assertIsNone(parsing.parse_unlink_callback("unlink:unknown"))
        self.assertIsNone(parsing.parse_unlink_callback("unlink:"))
        self.assertIsNone(parsing.parse_unlink_callback("other:yes"))
        self.assertIsNone(parsing.parse_unlink_callback(""))
        self.assertIsNone(parsing.parse_page_callback(""))

    def test_digest_all_callback_round_trip(self):
        callback_data = parsing.format_digest_all_callback(123)
        self.assertEqual(callback_data, "da:123")
        self.assertEqual(parsing.parse_digest_all_callback(callback_data), 123)

    def test_digest_all_callback_invalid(self):
        self.assertIsNone(parsing.parse_digest_all_callback("da:abc"))
        self.assertIsNone(parsing.parse_digest_all_callback("da:"))
        self.assertIsNone(parsing.parse_digest_all_callback("da"))
        self.assertIsNone(parsing.parse_digest_all_callback("other:123"))
        self.assertIsNone(parsing.parse_digest_all_callback(""))

    def test_digest_review_callback_round_trip(self):
        callback_data = parsing.format_digest_review_callback(123)
        self.assertEqual(callback_data, "drev:123")
        self.assertEqual(parsing.parse_digest_review_callback(callback_data), 123)

    def test_digest_review_callback_invalid(self):
        self.assertIsNone(parsing.parse_digest_review_callback("drev:abc"))
        self.assertIsNone(parsing.parse_digest_review_callback("drev:"))
        self.assertIsNone(parsing.parse_digest_review_callback("drev"))
        self.assertIsNone(parsing.parse_digest_review_callback("other:123"))
        self.assertIsNone(parsing.parse_digest_review_callback(""))

    def test_review_action_callback_round_trip(self):
        for act in ("y", "n", "s", "x"):
            data = parsing.format_review_action_callback(42, act)
            self.assertEqual(data, f"cr:42:{act}")
            self.assertEqual(parsing.parse_review_action_callback(data), (42, act))

    def test_review_action_callback_invalid(self):
        self.assertIsNone(parsing.parse_review_action_callback("cr:42:z"))
        self.assertIsNone(parsing.parse_review_action_callback("cr:abc:y"))
        self.assertIsNone(parsing.parse_review_action_callback("cr:42"))
        self.assertIsNone(parsing.parse_review_action_callback("other:42:y"))
        self.assertIsNone(parsing.parse_review_action_callback(""))


class KeyboardsTests(SimpleTestCase):
    """Pure unit tests for inline keyboard markup constructors."""

    def test_prompt_markup_structure(self):
        markup = keyboards.prompt_markup(88)
        self.assertIn("inline_keyboard", markup)
        row = markup["inline_keyboard"][0]
        self.assertEqual(len(row), 2)
        self.assertEqual(row[0]["text"], "✅ Still available")
        self.assertEqual(row[0]["callback_data"], "confirm_88")
        self.assertEqual(row[1]["text"], "❌ Sold out")
        self.assertEqual(row[1]["callback_data"], "deny_88")

    def test_digest_markup(self):
        markup = keyboards.digest_markup(42)
        rows = markup.inline_keyboard
        self.assertEqual(len(rows), 3)
        self.assertEqual(len(rows[0]), 1)
        self.assertEqual(rows[0][0].text, "✅ All still available")
        self.assertEqual(rows[0][0].callback_data, "da:42")
        self.assertEqual(len(rows[1]), 1)
        self.assertEqual(rows[1][0].text, "✏️ Check one by one")
        self.assertEqual(rows[1][0].callback_data, "drev:42")
        self.assertEqual(rows[2][0].callback_data, "menu:home")

    def test_review_markup(self):
        markup = keyboards.review_markup(99)
        rows = markup.inline_keyboard
        self.assertEqual(len(rows), 2)
        self.assertEqual(len(rows[0]), 2)
        self.assertEqual(rows[0][0].text, "✅ Available")
        self.assertEqual(rows[0][0].callback_data, "cr:99:y")
        self.assertEqual(rows[0][1].text, "❌ Sold")
        self.assertEqual(rows[0][1].callback_data, "cr:99:n")
        self.assertEqual(len(rows[1]), 2)
        self.assertEqual(rows[1][0].text, "⏭ Skip")
        self.assertEqual(rows[1][0].callback_data, "cr:99:s")
        self.assertEqual(rows[1][1].text, "⏹ Stop")
        self.assertEqual(rows[1][1].callback_data, "cr:99:x")

    def test_listing_markup_first_page_has_next_no_prev(self):
        product = types.SimpleNamespace(
            brand="Sony",
            name="WH-1000XM5",
            pk=10,
            shop=types.SimpleNamespace(name="TechZone"),
        )
        markup = keyboards.listing_markup([product], page=0, pages=3, label_shop=True)
        rows = markup.inline_keyboard
        self.assertEqual(len(rows), 3)

        product_row = rows[0]
        self.assertEqual(len(product_row), 2)
        self.assertEqual(product_row[0].text, "✅ TechZone — Sony WH-1000XM5")
        self.assertEqual(product_row[0].callback_data, "pconfirm:10:0")
        self.assertEqual(product_row[1].text, "❌")
        self.assertEqual(product_row[1].callback_data, "pdeny:10:0")

        nav_row = rows[1]
        self.assertEqual(len(nav_row), 1)
        self.assertEqual(nav_row[0].text, "Next ▶")
        self.assertEqual(nav_row[0].callback_data, "page:1")

    def test_listing_markup_middle_page_has_prev_and_next(self):
        product = types.SimpleNamespace(
            brand="Apple",
            name="iPad",
            pk=20,
            shop=types.SimpleNamespace(name="iStore"),
        )
        markup = keyboards.listing_markup([product], page=1, pages=3, label_shop=False)
        rows = markup.inline_keyboard
        self.assertEqual(len(rows), 3)

        product_row = rows[0]
        self.assertEqual(product_row[0].text, "✅ Apple iPad")
        self.assertEqual(product_row[0].callback_data, "pconfirm:20:1")

        nav_row = rows[1]
        self.assertEqual(len(nav_row), 2)
        self.assertEqual(nav_row[0].text, "◀ Prev")
        self.assertEqual(nav_row[0].callback_data, "page:0")
        self.assertEqual(nav_row[1].text, "Next ▶")
        self.assertEqual(nav_row[1].callback_data, "page:2")

    def test_listing_markup_last_page_has_prev_no_next(self):
        product = types.SimpleNamespace(
            brand="Apple",
            name="iPad",
            pk=20,
            shop=types.SimpleNamespace(name="iStore"),
        )
        markup = keyboards.listing_markup([product], page=2, pages=3, label_shop=False)
        rows = markup.inline_keyboard
        nav_row = rows[1]
        self.assertEqual(len(nav_row), 1)
        self.assertEqual(nav_row[0].text, "◀ Prev")
        self.assertEqual(nav_row[0].callback_data, "page:1")

    def test_listing_markup_truncates_long_label(self):
        very_long_name = "X" * 100
        product = types.SimpleNamespace(
            brand="LongBrand",
            name=very_long_name,
            pk=30,
            shop=types.SimpleNamespace(name="Shop"),
        )
        markup = keyboards.listing_markup([product], page=0, pages=1, label_shop=False)
        button_text = markup.inline_keyboard[0][0].text
        self.assertLessEqual(len(button_text), keyboards.BUTTON_LABEL_LIMIT)

    def test_unlink_confirmation_markup(self):
        markup = keyboards.unlink_confirmation_markup()
        rows = markup.inline_keyboard
        self.assertEqual(len(rows), 1)
        self.assertEqual(len(rows[0]), 2)
        self.assertEqual(rows[0][0].text, "Yes, unlink")
        self.assertEqual(rows[0][0].callback_data, "unlink:yes")
        self.assertEqual(rows[0][1].text, "Cancel")
        self.assertEqual(rows[0][1].callback_data, "unlink:cancel")


class ShopStatusTests(BotTestCase):
    """Tests for bot.services.shop_status."""

    def test_shop_status_counts_fresh_due_and_hidden(self):
        now = timezone.now()
        Product.objects.filter(shop=self.shop).delete()

        # 1. Fresh (age <= 7d): 2 days ago
        self._product("P Fresh", "p-fresh", self.shop, now - timedelta(days=2))
        # 2. Due (7d < age <= 14d): 9 days ago
        self._product("P Due", "p-due", self.shop, now - timedelta(days=9))
        # 3. Hidden (age > 14d): 15 days ago
        self._product("P Hidden", "p-hidden", self.shop, now - timedelta(days=15))
        # 4. Hidden (never confirmed): last_confirmed_at=None
        self._product("P Never", "p-never", self.shop, None)

        # 5. Excluded: unavailable
        p_unavail = self._product(
            "P Unavail", "p-unavail", self.shop, now - timedelta(days=2)
        )
        p_unavail.is_available = False
        p_unavail.save(update_fields=["is_available"])

        # 6. Excluded: non-LIVE (draft, sold)
        p_draft = self._product(
            "P Draft", "p-draft", self.shop, now - timedelta(days=2)
        )
        p_draft.status = ProductStatus.DRAFT
        p_draft.save(update_fields=["status"])

        p_sold = self._product("P Sold", "p-sold", self.shop, now - timedelta(days=2))
        p_sold.status = ProductStatus.SOLD
        p_sold.save(update_fields=["status"])

        status_data = shop_status(self.shop, now=now)
        self.assertEqual(status_data["fresh"], 1)
        self.assertEqual(status_data["due"], 1)
        self.assertEqual(status_data["hidden"], 2)  # p_hidden and p_never
        self.assertIsNone(status_data["last_checkin"])

    def test_shop_status_exact_boundaries_at_7d_and_14d(self):
        now = timezone.now()
        Product.objects.filter(shop=self.shop).delete()

        # Exactly 7 days ago -> age <= 7d -> fresh
        self._product("P 7d", "p-7d", self.shop, now - timedelta(days=7))
        # Exactly 14 days ago -> 7d < age <= 14d -> due
        self._product("P 14d", "p-14d", self.shop, now - timedelta(days=14))

        status_data = shop_status(self.shop, now=now)
        self.assertEqual(status_data["fresh"], 1)
        self.assertEqual(status_data["due"], 1)
        self.assertEqual(status_data["hidden"], 0)

    def test_shop_status_last_checkin_returns_latest_responded_at(self):
        ConfirmationRequest.objects.filter(shop=self.shop).delete()

        t1 = timezone.now() - timedelta(days=5)
        t2 = timezone.now() - timedelta(days=1)
        ConfirmationRequest.objects.create(
            shop=self.shop,
            product=self.fresh_product,
            status="confirmed",
            responded_at=t1,
        )
        ConfirmationRequest.objects.create(
            shop=self.shop,
            product=self.fresh_product,
            status="sold_out",
            responded_at=t2,
        )
        ConfirmationRequest.objects.create(
            shop=self.shop,
            product=self.fresh_product,
            status="pending",
            responded_at=None,
        )

        status_data = shop_status(self.shop)
        self.assertEqual(status_data["last_checkin"], t2)

    def test_shop_status_scoped_to_shop(self):
        other_shop = Shop.objects.create(
            name="Other Shop", owner=self.user, telegram_chat_id="555"
        )
        self._product("Other Product", "other-product", other_shop, timezone.now())
        ConfirmationRequest.objects.create(
            shop=other_shop,
            product=Product.objects.filter(shop=other_shop).first(),
            status="confirmed",
            responded_at=timezone.now(),
        )

        Product.objects.filter(shop=self.shop).delete()
        ConfirmationRequest.objects.filter(shop=self.shop).delete()

        status_data = shop_status(self.shop)
        self.assertEqual(status_data["fresh"], 0)
        self.assertEqual(status_data["due"], 0)
        self.assertEqual(status_data["hidden"], 0)
        self.assertIsNone(status_data["last_checkin"])


class StatusCommandTests(BotTestCase):
    """PTB command handler wiring for /status and /help."""

    def _message_update(self, chat_id=CHAT_ID):
        update = mock.Mock()
        update.message = mock.AsyncMock()
        update.effective_chat.id = int(chat_id)
        return update, update.message

    def test_status_unlinked_chat_replies_not_linked(self):
        update, message = self._message_update(chat_id=OTHER_CHAT_ID)
        async_to_sync(status)(update, None)

        message.reply_text.assert_awaited_once_with(messages.not_linked())

    def test_status_single_shop_replies_one_line_without_shop_prefix(self):
        update, message = self._message_update(chat_id=CHAT_ID)
        async_to_sync(status)(update, None)

        message.reply_text.assert_awaited_once()
        text = message.reply_text.call_args[0][0]
        self.assertFalse(text.startswith(f"{self.shop.name}:"))
        self.assertIn("fresh", text)
        self.assertIn("due soon", text)
        self.assertIn("hidden", text)
        self.assertIn("Last check-in:", text)

    def test_status_multiple_shops_prefixes_each_line(self):
        second_shop = Shop.objects.create(
            name="Mercato Gadgets", owner=self.user, telegram_chat_id=CHAT_ID
        )
        update, message = self._message_update(chat_id=CHAT_ID)
        async_to_sync(status)(update, None)

        message.reply_text.assert_awaited_once()
        text = message.reply_text.call_args[0][0]
        lines = text.strip().split("\n")
        self.assertEqual(len(lines), 2)
        self.assertTrue(lines[0].startswith(f"{self.shop.name}:"))
        self.assertTrue(lines[1].startswith(f"{second_shop.name}:"))

    def test_help_command_replies_help_text(self):
        update, message = self._message_update(chat_id=CHAT_ID)
        async_to_sync(help)(update, None)

        message.reply_text.assert_awaited_once_with(text=messages.help_text())


class MenuParsingTests(SimpleTestCase):
    def test_menu_callback_round_trip(self):
        for action in parsing.MENU_ACTIONS:
            data = parsing.format_menu_callback(action)
            self.assertEqual(data, f"menu:{action}")
            self.assertEqual(parsing.parse_menu_callback(data), action)

    def test_menu_callback_invalid(self):
        self.assertIsNone(parsing.parse_menu_callback("menu:bogus"))
        self.assertIsNone(parsing.parse_menu_callback("menu:"))
        self.assertIsNone(parsing.parse_menu_callback("other:home"))
        self.assertIsNone(parsing.parse_menu_callback(""))


class MenuKeyboardTests(SimpleTestCase):
    def test_menu_markup(self):
        rows = keyboards.menu_markup().inline_keyboard
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0][0].callback_data, "menu:products")
        self.assertEqual(rows[0][1].callback_data, "menu:status")
        self.assertEqual(rows[1][0].callback_data, "menu:help")
        self.assertEqual(rows[1][1].callback_data, "menu:unlink")


class BotCommandsListTests(SimpleTestCase):
    def test_bot_commands_are_well_formed(self):
        self.assertTrue(messages.BOT_COMMANDS)
        names = [command for command, _ in messages.BOT_COMMANDS]
        self.assertIn("menu", names)
        self.assertIn("myproducts", names)
        for command, description in messages.BOT_COMMANDS:
            self.assertFalse(command.startswith("/"))
            self.assertTrue(description)
        self.assertEqual(len(names), len(set(names)))

    def test_run_bot_registers_the_listed_commands(self):
        """The advertised list must match the handlers run_bot registers."""
        import inspect

        from bot.management.commands import run_bot as run_bot_module

        source = inspect.getsource(run_bot_module.Command.handle)
        for command, _ in messages.BOT_COMMANDS:
            self.assertIn(f'CommandHandler("{command}"', source)


class MenuHandlerTests(BotTestCase):
    """PTB wiring for /menu and the menu:* callbacks."""

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

    def test_menu_command_shows_the_hub(self):
        update, message = self._message_update()

        async_to_sync(menu)(update, None)

        markup = message.reply_text.call_args.kwargs["reply_markup"]
        self.assertEqual(markup.inline_keyboard[0][0].callback_data, "menu:products")

    def test_menu_home_redraws_the_hub(self):
        update, query = self._callback_update("menu:home")

        async_to_sync(on_callback)(update, None)

        query.answer.assert_awaited()
        markup = query.edit_message_text.call_args.kwargs["reply_markup"]
        self.assertEqual(markup.inline_keyboard[0][0].callback_data, "menu:products")

    def test_menu_products_opens_the_listing(self):
        update, query = self._callback_update("menu:products")

        async_to_sync(on_callback)(update, None)

        query.edit_message_text.assert_awaited()

    def test_menu_status_shows_status(self):
        update, query = self._callback_update("menu:status")

        async_to_sync(on_callback)(update, None)

        query.edit_message_text.assert_awaited()

    def test_menu_help_shows_help(self):
        update, query = self._callback_update("menu:help")

        async_to_sync(on_callback)(update, None)

        query.edit_message_text.assert_awaited_with(messages.help_text())

    def test_menu_unlink_shows_confirmation(self):
        update, query = self._callback_update("menu:unlink")

        async_to_sync(on_callback)(update, None)

        markup = query.edit_message_text.call_args.kwargs["reply_markup"]
        self.assertEqual(markup.inline_keyboard[0][0].callback_data, "unlink:yes")


class SyncBotCommandsTests(SimpleTestCase):
    def test_requires_token(self):
        with override_settings(TELEGRAM_BOT_TOKEN=""):
            with self.assertRaisesMessage(
                CommandError, "TELEGRAM_BOT_TOKEN is not set"
            ):
                call_command("sync_bot_commands")

    def test_registers_the_command_list(self):
        with override_settings(TELEGRAM_BOT_TOKEN="test-token"):
            with mock.patch("bot.telegram_api.set_my_commands") as set_commands:
                call_command("sync_bot_commands", stdout=StringIO())

        set_commands.assert_called_once_with(messages.BOT_COMMANDS)
