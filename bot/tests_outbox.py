"""Tests for the outbox, rate-limited sender, and transport error classification."""

from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import transaction
from django.test import TestCase, override_settings
from django.utils import timezone

from shops.models import Shop

from . import telegram_api
from .models import OutboundMessage
from .outbox import enqueue
from .sender import drain_outbox

User = get_user_model()


class OutboxEnqueueTests(TestCase):
    def test_enqueue_creates_queued_message(self):
        msg = enqueue(
            chat_id="123456",
            kind="prompt",
            payload={"text": "Is this available?"},
            dedupe_key="prompt:1:2026-09-29",
        )
        self.assertIsNotNone(msg)
        self.assertEqual(msg.status, "queued")
        self.assertEqual(msg.chat_id, "123456")
        self.assertEqual(msg.kind, "prompt")
        self.assertEqual(msg.payload, {"text": "Is this available?"})
        self.assertGreaterEqual(
            timezone.now(), msg.next_attempt_at - timedelta(seconds=2)
        )

    def test_enqueue_duplicate_dedupe_key_is_noop(self):
        msg1 = enqueue(
            chat_id="123456",
            kind="prompt",
            payload={"text": "First"},
            dedupe_key="unique-key",
        )
        self.assertIsNotNone(msg1)

        msg2 = enqueue(
            chat_id="123456",
            kind="prompt",
            payload={"text": "Second"},
            dedupe_key="unique-key",
        )
        self.assertIsNone(msg2)
        self.assertEqual(
            OutboundMessage.objects.filter(dedupe_key="unique-key").count(), 1
        )

    def test_duplicate_enqueue_inside_transaction_does_not_abort_transaction(self):
        with transaction.atomic():
            enqueue("123456", "prompt", {"text": "One"}, "tx-key")
            # Second call hits duplicate dedupe_key but should not invalidate transaction
            second = enqueue("123456", "prompt", {"text": "Two"}, "tx-key")
            self.assertIsNone(second)
            # Can still perform database writes in the same transaction
            enqueue("123456", "prompt", {"text": "Three"}, "tx-key-2")

        self.assertEqual(OutboundMessage.objects.filter(chat_id="123456").count(), 2)


class TransportErrorClassificationTests(TestCase):
    def test_parse_retry_after(self):
        err = telegram_api._parse_telegram_error(
            429,
            "Too Many Requests: retry after 25",
            {"retry_after": 25},
        )
        self.assertIsInstance(err, telegram_api.TelegramRetryAfter)
        self.assertIsInstance(err, telegram_api.TelegramError)
        self.assertEqual(err.retry_after, 25)

    def test_parse_forbidden(self):
        err = telegram_api._parse_telegram_error(
            403,
            "Forbidden: bot was blocked by the user",
        )
        self.assertIsInstance(err, telegram_api.TelegramForbidden)
        self.assertIsInstance(err, telegram_api.TelegramError)

    def test_parse_bad_request(self):
        err = telegram_api._parse_telegram_error(
            400,
            "Bad Request: chat not found",
        )
        self.assertIsInstance(err, telegram_api.TelegramBadRequest)
        self.assertIsInstance(err, telegram_api.TelegramError)

    def test_parse_generic_error(self):
        err = telegram_api._parse_telegram_error(
            500,
            "Internal Server Error",
        )
        self.assertIsInstance(err, telegram_api.TelegramError)
        self.assertNotIsInstance(err, telegram_api.TelegramBadRequest)


@override_settings(
    TELEGRAM_BOT_TOKEN="test-token",
    TELEGRAM_MOCK_SEND=False,
)
class SenderDrainTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="merchant", password="password123"
        )
        self.shop = Shop.objects.create(
            name="Test Shop",
            owner=self.user,
            telegram_chat_id="999888",
        )

    @patch("bot.telegram_api.send_message")
    def test_drain_success_marks_sent(self, mock_send):
        mock_send.return_value = {"message_id": 4321}

        msg = enqueue("999888", "prompt", {"text": "Hello"}, "k1")
        count = drain_outbox(rate_limit=False)

        self.assertEqual(count, 1)
        msg.refresh_from_db()
        self.assertEqual(msg.status, "sent")
        self.assertEqual(msg.telegram_message_id, "4321")
        self.assertEqual(msg.attempts, 1)
        self.assertIsNone(msg.claimed_at)
        mock_send.assert_called_once_with(
            chat_id="999888", text="Hello", reply_markup=None
        )

    @patch("bot.telegram_api.send_message")
    def test_drain_success_sets_digest_telegram_message_id(self, mock_send):
        from bot.models import ConfirmationDigest

        mock_send.return_value = {"message_id": 5566}

        today = timezone.localtime(timezone.now()).date()
        digest = ConfirmationDigest.objects.create(shop=self.shop, for_date=today)

        msg = enqueue(
            "999888",
            "digest",
            {"text": "Digest text", "digest_id": digest.pk},
            "k-digest",
        )
        count = drain_outbox(rate_limit=False)

        self.assertEqual(count, 1)
        msg.refresh_from_db()
        self.assertEqual(msg.status, "sent")
        self.assertEqual(msg.telegram_message_id, "5566")

        digest.refresh_from_db()
        self.assertEqual(digest.telegram_message_id, "5566")

    @patch("bot.telegram_api.send_message")
    def test_drain_skips_blocked_chat_and_marks_cancelled(self, mock_send):
        self.shop.blocked_at = timezone.now()
        self.shop.save(update_fields=["blocked_at"])

        msg = enqueue("999888", "prompt", {"text": "Hello"}, "k2")
        count = drain_outbox(rate_limit=False)

        self.assertEqual(count, 1)
        msg.refresh_from_db()
        self.assertEqual(msg.status, "cancelled")
        self.assertIn("blocked", msg.last_error.lower())
        mock_send.assert_not_called()

    @patch("bot.telegram_api.send_message")
    def test_drain_retry_after_reschedules(self, mock_send):
        mock_send.side_effect = telegram_api.TelegramRetryAfter(30, "Too Many Requests")

        msg = enqueue("999888", "prompt", {"text": "Hello"}, "k3")
        drain_outbox(rate_limit=False)

        msg.refresh_from_db()
        self.assertEqual(msg.status, "queued")
        self.assertEqual(msg.attempts, 1)
        self.assertGreater(msg.next_attempt_at, timezone.now() + timedelta(seconds=25))
        self.assertIsNone(msg.claimed_at)

    @patch("bot.telegram_api.send_message")
    def test_drain_forbidden_marks_dead_and_sets_shop_blocked(self, mock_send):
        mock_send.side_effect = telegram_api.TelegramForbidden("bot blocked by user")

        msg = enqueue("999888", "prompt", {"text": "Hello"}, "k4")
        drain_outbox(rate_limit=False)

        msg.refresh_from_db()
        self.assertEqual(msg.status, "dead")
        self.assertIn("Forbidden", msg.last_error)

        self.shop.refresh_from_db()
        self.assertIsNotNone(self.shop.blocked_at)

    @patch("bot.telegram_api.send_message")
    def test_drain_bad_request_marks_dead_without_retry(self, mock_send):
        mock_send.side_effect = telegram_api.TelegramBadRequest(
            "Bad Request: chat not found"
        )

        msg = enqueue("999888", "prompt", {"text": "Hello"}, "k5")
        drain_outbox(rate_limit=False)

        msg.refresh_from_db()
        self.assertEqual(msg.status, "dead")
        self.assertIn("Bad Request", msg.last_error)

    @patch("bot.telegram_api.send_message")
    def test_drain_network_error_backoff_and_dead_after_max(self, mock_send):
        mock_send.side_effect = telegram_api.TelegramNetworkError(
            "Connection timed out"
        )

        msg = enqueue("999888", "prompt", {"text": "Hello"}, "k6")

        # 1st attempt: 15s backoff
        drain_outbox(rate_limit=False)
        msg.refresh_from_db()
        self.assertEqual(msg.status, "queued")
        self.assertEqual(msg.attempts, 1)
        self.assertGreater(msg.next_attempt_at, timezone.now() + timedelta(seconds=10))

        # Set attempts to 5 and next_attempt_at to past
        msg.attempts = 5
        msg.next_attempt_at = timezone.now() - timedelta(seconds=1)
        msg.save()

        # 6th attempt: should mark dead
        drain_outbox(rate_limit=False)
        msg.refresh_from_db()
        self.assertEqual(msg.status, "dead")
        self.assertEqual(msg.attempts, 6)

    def test_abandoned_sending_row_is_reclaimed(self):
        # Row stuck in 'sending' for 10 minutes (crashed worker)
        old_claim = timezone.now() - timedelta(minutes=10)
        msg = OutboundMessage.objects.create(
            chat_id="999888",
            kind="prompt",
            payload={"text": "Stuck"},
            dedupe_key="stuck-msg",
            status="sending",
            claimed_at=old_claim,
            next_attempt_at=timezone.now() - timedelta(minutes=15),
        )

        with patch("bot.telegram_api.send_message") as mock_send:
            mock_send.return_value = {"message_id": 99}
            drain_outbox(rate_limit=False)

        msg.refresh_from_db()
        # Should have been reclaimed to queued and then processed to sent
        self.assertEqual(msg.status, "sent")
        self.assertEqual(msg.telegram_message_id, "99")


@override_settings(
    TELEGRAM_BOT_TOKEN="test-token",
    TELEGRAM_MOCK_SEND=False,
)
class ConcurrentDrainTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="merchant_concurrent", password="password123"
        )
        self.shop = Shop.objects.create(
            name="Concurrent Shop",
            owner=self.user,
            telegram_chat_id="999888",
        )

    @patch("bot.telegram_api.send_message")
    def test_concurrent_claim_does_not_double_send(self, mock_send):
        mock_send.return_value = {"message_id": 100}
        msg1 = enqueue("999888", "prompt", {"text": "M1"}, "c1")
        msg2 = enqueue("999888", "prompt", {"text": "M2"}, "c2")

        # Simulate Worker 1 having claimed msg1 in the short transaction
        msg1.status = "sending"
        msg1.claimed_at = timezone.now()
        msg1.save(update_fields=["status", "claimed_at"])

        # Worker 2 drains outbox concurrently
        drain_outbox(rate_limit=False)

        msg2.refresh_from_db()
        self.assertEqual(msg2.status, "sent")

        msg1.refresh_from_db()
        # msg1 was already claimed as sending, so worker 2 did not touch it
        self.assertEqual(msg1.status, "sending")


class OutboundMessageAdminTests(TestCase):
    def test_requeue_action_reschedules_dead_and_cancelled_messages(self):
        from django.contrib.admin.sites import AdminSite

        from bot.admin import OutboundMessageAdmin

        site = AdminSite()
        admin_obj = OutboundMessageAdmin(OutboundMessage, site)

        msg_dead = enqueue("123", "prompt", {"text": "D"}, "d1")
        msg_dead.status = "dead"
        msg_dead.last_error = "some error"
        msg_dead.save()

        msg_canc = enqueue("123", "prompt", {"text": "C"}, "c1")
        msg_canc.status = "cancelled"
        msg_canc.save()

        msg_sent = enqueue("123", "prompt", {"text": "S"}, "s1")
        msg_sent.status = "sent"
        msg_sent.save()

        from unittest.mock import MagicMock

        mock_request = MagicMock()

        admin_obj.requeue_messages(mock_request, OutboundMessage.objects.all())

        msg_dead.refresh_from_db()
        self.assertEqual(msg_dead.status, "queued")
        self.assertEqual(msg_dead.last_error, "")

        msg_canc.refresh_from_db()
        self.assertEqual(msg_canc.status, "queued")

        msg_sent.refresh_from_db()
        # sent row remains sent
        self.assertEqual(msg_sent.status, "sent")


@override_settings(TELEGRAM_BOT_TOKEN="", TELEGRAM_MOCK_SEND=False)
class UnconfiguredSenderTests(TestCase):
    """A missing token must never look like a successful send."""

    def test_unconfigured_token_marks_dead_not_sent(self):
        msg = enqueue("555000", "prompt", {"text": "Hello"}, "no-token")

        count = drain_outbox(rate_limit=False)

        self.assertEqual(count, 1)
        msg.refresh_from_db()
        self.assertEqual(msg.status, "dead")
        self.assertNotEqual(msg.status, "sent")
        self.assertIn("TELEGRAM_BOT_TOKEN", msg.last_error)
        self.assertIsNone(msg.claimed_at)
