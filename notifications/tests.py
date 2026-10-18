"""Tests for the notifications service, channel delivery, deduplication, and lifecycle."""

from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.management import call_command
from django.db import transaction
from django.test import TestCase, override_settings
from django.utils import timezone
from django_celery_beat.models import PeriodicTask

from bot.models import OutboundMessage
from notifications.kinds import TELEGRAM, Kind
from notifications.models import Notification
from notifications.services import (
    mark_read,
    notify,
    purge_old_notifications,
    send_email_digests,
)
from notifications.tasks import (
    purge_old_notifications as purge_task,
)
from notifications.tasks import (
    send_email_digests as digest_task,
)
from shops.models import Shop, VerificationRequest

User = get_user_model()


class KindValidationTests(TestCase):
    def test_kind_with_telegram_requires_telegram_text(self):
        with self.assertRaises(ValueError):
            Kind(
                name="invalid_kind",
                channels=(TELEGRAM,),
                dedupe_key=lambda p: "k",
                link=lambda p: "/link",
                telegram_text=None,
            )


class NotificationServiceTests(TestCase):
    def setUp(self):
        self.shop_owner = User.objects.create_user(
            username="owner_user",
            email="owner@example.com",
            password="password123",
        )
        self.buyer = User.objects.create_user(
            username="buyer_user",
            email="buyer@example.com",
            password="password123",
        )
        self.shop = Shop.objects.create(
            name="Alpha Store",
            owner=self.shop_owner,
            telegram_chat_id="111222",
        )

    def test_each_kind_creates_notification_with_right_fields(self):
        # 1. inquiry_message
        n1 = notify(
            self.shop_owner,
            "inquiry_message",
            {
                "message_id": 101,
                "inquiry_id": 1,
                "shop_id": self.shop.pk,
                "product_name": "Leather Shoes",
                "body": "Do you have size 42?",
            },
        )
        self.assertIsNotNone(n1)
        self.assertEqual(n1.recipient, self.shop_owner)
        self.assertEqual(n1.kind, "inquiry_message")
        self.assertEqual(n1.link, "/dashboard/inquiries/1")
        self.assertEqual(n1.dedupe_key, "inquiry_message:101")

        outbox1 = OutboundMessage.objects.get(
            dedupe_key=f"notification:{n1.pk}:telegram"
        )
        self.assertEqual(outbox1.chat_id, "111222")
        self.assertEqual(outbox1.kind, "notify:inquiry_message")
        self.assertTrue(outbox1.send_silently_at_night)
        self.assertIn("Leather Shoes", outbox1.payload.get("text", ""))
        self.assertIn("Do you have size 42?", outbox1.payload.get("text", ""))
        self.assertIn("/dashboard/inquiries/1", outbox1.payload.get("text", ""))

        # 2. inquiry_reply
        n2 = notify(
            self.buyer,
            "inquiry_reply",
            {
                "message_id": 202,
                "inquiry_id": 1,
            },
        )
        self.assertIsNotNone(n2)
        self.assertEqual(n2.recipient, self.buyer)
        self.assertEqual(n2.kind, "inquiry_reply")
        self.assertEqual(n2.link, "/inquiries/1")
        self.assertEqual(n2.dedupe_key, "inquiry_reply:202")
        # Email kind, no Telegram outbox row
        self.assertFalse(
            OutboundMessage.objects.filter(
                dedupe_key=f"notification:{n2.pk}:telegram"
            ).exists()
        )

        # 3. inquiry_unanswered
        n3 = notify(
            self.shop_owner,
            "inquiry_unanswered",
            {
                "inquiry_id": 1,
                "shop_id": self.shop.pk,
                "product_name": "Leather Shoes",
            },
        )
        self.assertIsNotNone(n3)
        self.assertEqual(n3.recipient, self.shop_owner)
        self.assertEqual(n3.kind, "inquiry_unanswered")
        self.assertEqual(n3.link, "/dashboard/inquiries/1")
        self.assertEqual(n3.dedupe_key, "inquiry_unanswered:1")

        outbox3 = OutboundMessage.objects.get(
            dedupe_key=f"notification:{n3.pk}:telegram"
        )
        self.assertEqual(outbox3.chat_id, "111222")
        self.assertEqual(outbox3.kind, "notify:inquiry_unanswered")
        self.assertFalse(outbox3.send_silently_at_night)  # Held until 07:00
        self.assertIn("Leather Shoes", outbox3.payload.get("text", ""))

        # 4. verification_reviewed
        n4 = notify(
            self.shop_owner,
            "verification_reviewed",
            {
                "shop_id": self.shop.pk,
                "shop_slug": self.shop.slug,
                "request_id": 50,
                "state": "approved",
            },
        )
        self.assertIsNotNone(n4)
        self.assertEqual(n4.recipient, self.shop_owner)
        self.assertEqual(n4.kind, "verification_reviewed")
        self.assertEqual(n4.link, f"/dashboard/shop/{self.shop.slug}/verification")
        self.assertEqual(n4.dedupe_key, "verification:50:approved")

        outbox4 = OutboundMessage.objects.get(
            dedupe_key=f"notification:{n4.pk}:telegram"
        )
        self.assertEqual(outbox4.chat_id, "111222")
        self.assertEqual(outbox4.kind, "notify:verification_reviewed")
        self.assertFalse(outbox4.send_silently_at_night)  # Held until 07:00
        self.assertIn("approved", outbox4.payload.get("text", ""))

    def test_deduplication_returns_none_and_single_outbox(self):
        payload = {
            "message_id": 303,
            "inquiry_id": 10,
            "shop_id": self.shop.pk,
            "body": "First",
        }
        first = notify(self.shop_owner, "inquiry_message", payload)
        self.assertIsNotNone(first)

        second = notify(self.shop_owner, "inquiry_message", payload)
        self.assertIsNone(second)

        self.assertEqual(
            Notification.objects.filter(
                recipient=self.shop_owner, dedupe_key="inquiry_message:303"
            ).count(),
            1,
        )
        self.assertEqual(
            OutboundMessage.objects.filter(
                dedupe_key=f"notification:{first.pk}:telegram"
            ).count(),
            1,
        )

    def test_same_dedupe_key_for_different_users(self):
        payload = {"message_id": 404, "inquiry_id": 15}
        n1 = notify(self.shop_owner, "inquiry_reply", payload)
        n2 = notify(self.buyer, "inquiry_reply", payload)

        self.assertIsNotNone(n1)
        self.assertIsNotNone(n2)
        self.assertEqual(n1.dedupe_key, n2.dedupe_key)
        self.assertNotEqual(n1.recipient, n2.recipient)

    def test_inquiry_unanswered_dedupe_per_conversation(self):
        payload = {"inquiry_id": 77, "shop_id": self.shop.pk}
        first = notify(self.shop_owner, "inquiry_unanswered", payload)
        self.assertIsNotNone(first)

        second = notify(self.shop_owner, "inquiry_unanswered", payload)
        self.assertIsNone(second)

    def test_unknown_kind_raises_value_error(self):
        with self.assertRaises(ValueError):
            notify(self.shop_owner, "non_existent_kind", {"foo": "bar"})

    def test_caller_transaction_rollback_cleans_notification_and_outbox(self):
        try:
            with transaction.atomic():
                notify(
                    self.shop_owner,
                    "inquiry_message",
                    {
                        "message_id": 505,
                        "inquiry_id": 20,
                        "shop_id": self.shop.pk,
                    },
                )
                raise RuntimeError("Simulated transaction rollback")
        except RuntimeError:
            pass

        self.assertFalse(
            Notification.objects.filter(dedupe_key="inquiry_message:505").exists()
        )
        self.assertFalse(
            OutboundMessage.objects.filter(kind="notify:inquiry_message").exists()
        )

    def test_telegram_channel_shop_missing_or_unlinked_or_blocked(self):
        # 1. Shop has no telegram_chat_id
        self.shop.telegram_chat_id = ""
        self.shop.save(update_fields=["telegram_chat_id"])

        n1 = notify(
            self.shop_owner,
            "inquiry_message",
            {"message_id": 601, "inquiry_id": 30, "shop_id": self.shop.pk},
        )
        self.assertIsNotNone(n1)
        self.assertFalse(
            OutboundMessage.objects.filter(
                dedupe_key=f"notification:{n1.pk}:telegram"
            ).exists()
        )

        # 2. Shop is blocked by merchant
        self.shop.telegram_chat_id = "111222"
        self.shop.blocked_at = timezone.now()
        self.shop.save(update_fields=["telegram_chat_id", "blocked_at"])

        n2 = notify(
            self.shop_owner,
            "inquiry_message",
            {"message_id": 602, "inquiry_id": 31, "shop_id": self.shop.pk},
        )
        self.assertIsNotNone(n2)
        self.assertFalse(
            OutboundMessage.objects.filter(
                dedupe_key=f"notification:{n2.pk}:telegram"
            ).exists()
        )

        # 3. shop_id not owned by recipient (IDOR protection)
        self.shop.blocked_at = None
        self.shop.save(update_fields=["blocked_at"])
        other_user = User.objects.create_user(username="other", password="password123")

        n3 = notify(
            other_user,
            "inquiry_message",
            {"message_id": 603, "inquiry_id": 32, "shop_id": self.shop.pk},
        )
        self.assertIsNotNone(n3)
        self.assertFalse(
            OutboundMessage.objects.filter(
                dedupe_key=f"notification:{n3.pk}:telegram"
            ).exists()
        )

        # 4. shop_id missing from payload
        n4 = notify(
            self.shop_owner,
            "inquiry_message",
            {"message_id": 604, "inquiry_id": 33},
        )
        self.assertIsNotNone(n4)
        self.assertFalse(
            OutboundMessage.objects.filter(
                dedupe_key=f"notification:{n4.pk}:telegram"
            ).exists()
        )


class MarkReadTests(TestCase):
    def setUp(self):
        self.user1 = User.objects.create_user(username="u1", password="password123")
        self.user2 = User.objects.create_user(username="u2", password="password123")
        self.notif = Notification.objects.create(
            recipient=self.user1,
            kind="inquiry_reply",
            payload={"inquiry_id": 1},
            link="/inquiries/1",
            dedupe_key="k1",
        )

    def test_owner_marks_read_sets_read_at(self):
        self.assertIsNone(self.notif.read_at)
        updated = mark_read(self.user1, self.notif.pk)
        self.assertIsNotNone(updated.read_at)

        self.notif.refresh_from_db()
        self.assertIsNotNone(self.notif.read_at)

    def test_other_user_marking_read_raises_does_not_exist(self):
        with self.assertRaises(Notification.DoesNotExist):
            mark_read(self.user2, self.notif.pk)

    def test_missing_id_raises_does_not_exist(self):
        with self.assertRaises(Notification.DoesNotExist):
            mark_read(self.user1, 999999)


@override_settings(
    NOTIFICATION_EMAIL_DELAY_MINUTES=15,
    DEFAULT_FROM_EMAIL="Tinsae <no-reply@tinsae.et>",
    FRONTEND_URL="http://localhost:3000",
)
class EmailDigestTests(TestCase):
    def setUp(self):
        self.buyer = User.objects.create_user(
            username="buyer_digest",
            email="buyer@example.com",
            password="password123",
        )
        mail.outbox.clear()

    def test_email_digest_batches_replies_for_recipient(self):
        now = timezone.now()
        n1 = Notification.objects.create(
            recipient=self.buyer,
            kind="inquiry_reply",
            payload={"message_id": 1, "inquiry_id": 10},
            link="/inquiries/10",
            dedupe_key="ir:1",
        )
        n2 = Notification.objects.create(
            recipient=self.buyer,
            kind="inquiry_reply",
            payload={"message_id": 2, "inquiry_id": 11},
            link="/inquiries/11",
            dedupe_key="ir:2",
        )
        # Age them past NOTIFICATION_EMAIL_DELAY_MINUTES
        Notification.objects.filter(pk__in=[n1.pk, n2.pk]).update(
            created_at=now - timedelta(minutes=20)
        )

        sent_count = send_email_digests()
        self.assertEqual(sent_count, 1)
        self.assertEqual(len(mail.outbox), 1)

        email = mail.outbox[0]
        self.assertEqual(email.to, ["buyer@example.com"])
        self.assertIn("2 unread notifications", email.subject)
        self.assertIn("http://localhost:3000/inquiries/10", email.body)
        self.assertIn("http://localhost:3000/inquiries/11", email.body)

        n1.refresh_from_db()
        n2.refresh_from_db()
        self.assertIsNotNone(n1.email_sent_at)
        self.assertIsNotNone(n2.email_sent_at)

        # Running again sends no second email
        repeat_count = send_email_digests()
        self.assertEqual(repeat_count, 0)
        self.assertEqual(len(mail.outbox), 1)

    def test_already_read_row_is_not_emailed(self):
        now = timezone.now()
        n = Notification.objects.create(
            recipient=self.buyer,
            kind="inquiry_reply",
            payload={"message_id": 3, "inquiry_id": 12},
            link="/inquiries/12",
            dedupe_key="ir:3",
            read_at=now - timedelta(minutes=10),
        )
        Notification.objects.filter(pk=n.pk).update(
            created_at=now - timedelta(minutes=25)
        )

        sent_count = send_email_digests()
        self.assertEqual(sent_count, 0)
        self.assertEqual(len(mail.outbox), 0)

        n.refresh_from_db()
        self.assertIsNone(n.email_sent_at)

    def test_young_row_under_delay_is_not_emailed_yet(self):
        now = timezone.now()
        n = Notification.objects.create(
            recipient=self.buyer,
            kind="inquiry_reply",
            payload={"message_id": 4, "inquiry_id": 13},
            link="/inquiries/13",
            dedupe_key="ir:4",
        )
        # Created only 5 minutes ago (< 15 min delay)
        Notification.objects.filter(pk=n.pk).update(
            created_at=now - timedelta(minutes=5)
        )

        sent_count = send_email_digests()
        self.assertEqual(sent_count, 0)
        self.assertEqual(len(mail.outbox), 0)

        n.refresh_from_db()
        self.assertIsNone(n.email_sent_at)

    def test_user_without_email_skipped_without_crash(self):
        no_email_user = User.objects.create_user(
            username="no_email_user",
            email="",
            password="password123",
        )
        now = timezone.now()
        n = Notification.objects.create(
            recipient=no_email_user,
            kind="inquiry_reply",
            payload={"message_id": 5, "inquiry_id": 14},
            link="/inquiries/14",
            dedupe_key="ir:5",
        )
        Notification.objects.filter(pk=n.pk).update(
            created_at=now - timedelta(minutes=30)
        )

        sent_count = send_email_digests()
        self.assertEqual(sent_count, 0)
        self.assertEqual(len(mail.outbox), 0)

        n.refresh_from_db()
        self.assertIsNone(n.email_sent_at)

    def test_deactivated_buyer_gets_no_email(self):
        inactive_user = User.objects.create_user(
            username="inactive_buyer",
            email="inactive@example.com",
            password="password123",
            is_active=False,
        )
        now = timezone.now()
        n = Notification.objects.create(
            recipient=inactive_user,
            kind="inquiry_reply",
            payload={"message_id": 6, "inquiry_id": 15},
            link="/inquiries/15",
            dedupe_key="ir:6",
        )
        Notification.objects.filter(pk=n.pk).update(
            created_at=now - timedelta(minutes=30)
        )

        sent_count = send_email_digests()
        self.assertEqual(sent_count, 0)
        self.assertEqual(len(mail.outbox), 0)

        n.refresh_from_db()
        self.assertIsNone(n.email_sent_at)

    def test_single_recipient_smtp_failure_does_not_starve_subsequent_recipients(self):
        buyer2 = User.objects.create_user(
            username="buyer2_digest",
            email="buyer2@example.com",
            password="password123",
        )
        now = timezone.now()
        n1 = Notification.objects.create(
            recipient=self.buyer,
            kind="inquiry_reply",
            payload={"message_id": 10, "inquiry_id": 20},
            link="/inquiries/20",
            dedupe_key="ir:10",
        )
        n2 = Notification.objects.create(
            recipient=buyer2,
            kind="inquiry_reply",
            payload={"message_id": 11, "inquiry_id": 21},
            link="/inquiries/21",
            dedupe_key="ir:11",
        )
        Notification.objects.filter(pk__in=[n1.pk, n2.pk]).update(
            created_at=now - timedelta(minutes=30)
        )

        # Mock send_mail: raise on first call, succeed on second call
        original_send_mail = mail.send_mail
        call_count = 0

        def flaky_send_mail(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("SMTP connection failure")
            return original_send_mail(*args, **kwargs)

        with patch("notifications.services.send_mail", side_effect=flaky_send_mail):
            sent_count = send_email_digests()

        # Task does not raise, second buyer received email
        self.assertEqual(sent_count, 1)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["buyer2@example.com"])

        n1.refresh_from_db()
        n2.refresh_from_db()
        # First buyer's rows keep email_sent_at=None (rolled back)
        self.assertIsNone(n1.email_sent_at)
        # Second buyer's rows get email_sent_at set
        self.assertIsNotNone(n2.email_sent_at)

    def test_celery_task_wrapper(self):
        result = digest_task()
        self.assertIsInstance(result, int)


@override_settings(NOTIFICATION_RETENTION_DAYS=90)
class PurgeNotificationsTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="purge_user", password="password123"
        )

    def test_purge_deletes_rows_older_than_retention_days(self):
        now = timezone.now()
        old_notif = Notification.objects.create(
            recipient=self.user,
            kind="inquiry_reply",
            payload={"inquiry_id": 1},
            dedupe_key="old:1",
        )
        Notification.objects.filter(pk=old_notif.pk).update(
            created_at=now - timedelta(days=95)
        )

        recent_notif = Notification.objects.create(
            recipient=self.user,
            kind="inquiry_reply",
            payload={"inquiry_id": 2},
            dedupe_key="recent:2",
        )
        Notification.objects.filter(pk=recent_notif.pk).update(
            created_at=now - timedelta(days=10)
        )

        deleted = purge_old_notifications()
        self.assertEqual(deleted, 1)

        self.assertFalse(Notification.objects.filter(pk=old_notif.pk).exists())
        self.assertTrue(Notification.objects.filter(pk=recent_notif.pk).exists())

    def test_celery_task_wrapper(self):
        result = purge_task()
        self.assertIsInstance(result, int)


class VerificationNotificationTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            username="v_owner", password="password123"
        )
        self.reviewer = User.objects.create_user(
            username="v_reviewer", password="password123", is_staff=True
        )
        self.shop = Shop.objects.create(
            name="Verification Test Shop",
            owner=self.owner,
            telegram_chat_id="333444",
        )

    def test_verification_approval_and_rejection_triggers_notification(self):
        # 1. Pending request creates NO notification
        vr = VerificationRequest.objects.create(
            shop=self.shop,
            state="pending",
        )
        self.assertEqual(
            Notification.objects.filter(
                recipient=self.owner, kind="verification_reviewed"
            ).count(),
            0,
        )

        # 2. Approve -> 1 notification for owner
        vr.state = "approved"
        vr.reviewer = self.reviewer
        vr.save()

        notifs = list(
            Notification.objects.filter(
                recipient=self.owner, kind="verification_reviewed"
            )
        )
        self.assertEqual(len(notifs), 1)
        self.assertEqual(notifs[0].payload["state"], "approved")
        self.assertEqual(notifs[0].dedupe_key, f"verification:{vr.pk}:approved")

        # 3. Re-saving approved request -> dedupe keeps it at 1
        vr.save()
        self.assertEqual(
            Notification.objects.filter(
                recipient=self.owner, kind="verification_reviewed"
            ).count(),
            1,
        )

        # 4. Reject -> creates 2nd notification with state="rejected"
        vr.state = "rejected"
        vr.rejection_reason = "Incomplete documents"
        vr.save()

        self.assertEqual(
            Notification.objects.filter(
                recipient=self.owner, kind="verification_reviewed"
            ).count(),
            2,
        )
        rejected_notif = Notification.objects.get(
            recipient=self.owner,
            kind="verification_reviewed",
            dedupe_key=f"verification:{vr.pk}:rejected",
        )
        self.assertEqual(rejected_notif.payload["state"], "rejected")


class NotificationScheduleCommandTests(TestCase):
    def test_setup_notification_schedules_is_idempotent(self):
        call_command("setup_notification_schedules")

        digest_pt = PeriodicTask.objects.get(name="notifications-send-email-digests")
        self.assertEqual(digest_pt.task, "notifications.tasks.send_email_digests")
        self.assertTrue(digest_pt.enabled)

        reminder_pt = PeriodicTask.objects.get(
            name="notifications-remind-unanswered-inquiries"
        )
        self.assertEqual(
            reminder_pt.task, "inquiries.tasks.remind_unanswered_inquiries"
        )
        self.assertTrue(reminder_pt.enabled)

        purge_pt = PeriodicTask.objects.get(
            name="notifications-purge-old-notifications"
        )
        self.assertEqual(purge_pt.task, "notifications.tasks.purge_old_notifications")
        self.assertTrue(purge_pt.enabled)

        # Re-running is idempotent
        call_command("setup_notification_schedules")
        self.assertEqual(
            PeriodicTask.objects.filter(
                name="notifications-send-email-digests"
            ).count(),
            1,
        )
        self.assertEqual(
            PeriodicTask.objects.filter(
                name="notifications-remind-unanswered-inquiries"
            ).count(),
            1,
        )
