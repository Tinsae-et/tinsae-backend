import hashlib
from datetime import timedelta
from unittest import mock

from django.conf import settings
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from bot.models import OutboundMessage
from catalog.models import Category, Product, ProductStatus
from inquiries import selectors
from inquiries.models import (
    ContactChannel,
    ContactIntent,
    Inquiry,
    InquiryMessage,
    SenderRole,
)
from inquiries.serializers import (
    BuyerInquiryRequestSerializer,
    InquiryMessageSerializer,
    InquiryReplySerializer,
    InquirySerializer,
)
from inquiries.services import (
    _hash_ip,
    close_inquiry,
    mark_read,
    record_contact_intent,
    remind_unanswered_inquiries,
    send_message,
    start_inquiry,
)
from notifications.models import Notification
from shops.models import Shop
from users.models import User

WEB = InquiryMessage.SentVia.WEB
TELEGRAM = InquiryMessage.SentVia.TELEGRAM


class InquiryTestCase(TestCase):
    def setUp(self):
        self.buyer = User.objects.create_user(username="buyer", password="pass")
        self.other_buyer = User.objects.create_user(username="buyer2", password="pass")
        self.owner = User.objects.create_user(username="owner", password="pass")
        self.other_owner = User.objects.create_user(username="owner2", password="pass")
        self.shop = Shop.objects.create(name="Test Shop", owner=self.owner)
        self.other_shop = Shop.objects.create(name="Other Shop", owner=self.other_owner)
        self.category = Category.objects.create(name="Phones", slug="phones")
        self.product = self._product(self.shop, "Phone A")
        self.second_product = self._product(self.shop, "Phone B")
        self.foreign_product = self._product(self.other_shop, "Phone C")

        patcher = mock.patch("inquiries.services.notify")
        self.notify = patcher.start()
        self.addCleanup(patcher.stop)

    def _product(self, shop, name, **kwargs):
        return Product.objects.create(
            shop=shop,
            category=self.category,
            brand="Acme",
            name=name,
            price="1000.00",
            **kwargs,
        )

    def _start(self, buyer=None, shop=None, body="Is this available?", **kwargs):
        return start_inquiry(
            buyer or self.buyer, shop or self.shop, body, WEB, **kwargs
        )

    def _send(self, user, inquiry_id, body="Hello", sent_via=WEB):
        return send_message(user, inquiry_id, body, sent_via)


class StartInquiryTests(InquiryTestCase):
    def test_creates_inquiry_with_first_buyer_message(self):
        inquiry = self._start(product=self.product)

        inquiry.refresh_from_db()
        message = inquiry.messages.get()
        self.assertEqual(message.sender_role, InquiryMessage.SenderRole.BUYER)
        self.assertEqual(inquiry.last_sender_role, SenderRole.BUYER)
        self.assertEqual(message.sent_via, WEB)
        self.assertEqual(inquiry.product, self.product)
        self.assertEqual(inquiry.status, Inquiry.Status.OPEN)
        self.assertEqual(inquiry.shop_unread, 1)
        self.assertEqual(inquiry.buyer_unread, 0)

    def test_notifies_the_shop_owner(self):
        inquiry = self._start(product=self.product, body="Interested!")

        message = inquiry.messages.get()
        self.notify.assert_called_once_with(
            self.owner,
            "inquiry_message",
            {
                "message_id": message.pk,
                "inquiry_id": inquiry.pk,
                "shop_id": self.shop.pk,
                "product_name": "Phone A",
                "body": "Interested!",
            },
        )

    def test_notifies_the_shop_owner_without_product(self):
        inquiry = self._start(body="General inquiry")

        message = inquiry.messages.get()
        self.notify.assert_called_once_with(
            self.owner,
            "inquiry_message",
            {
                "message_id": message.pk,
                "inquiry_id": inquiry.pk,
                "shop_id": self.shop.pk,
                "product_name": "",
                "body": "General inquiry",
            },
        )

    def test_writing_again_continues_the_open_conversation(self):
        first = self._start(product=self.product, body="First")
        again = self._start(product=self.product, body="Second")

        self.assertEqual(again.pk, first.pk)
        self.assertEqual(Inquiry.objects.count(), 1)
        self.assertEqual(
            list(again.messages.values_list("body", flat=True)), ["First", "Second"]
        )

    def test_writing_again_reopens_the_closed_conversation(self):
        inquiry = self._start(product=self.product)
        close_inquiry(self.buyer, inquiry.pk)

        again = self._start(product=self.product, body="Writing again")

        self.assertEqual(again.pk, inquiry.pk)
        self.assertEqual(Inquiry.objects.count(), 1)
        again.refresh_from_db()
        self.assertEqual(again.status, Inquiry.Status.OPEN)

    def test_prefers_an_open_conversation_over_a_closed_one(self):
        Inquiry.objects.create(
            buyer=self.buyer, shop=self.shop, status=Inquiry.Status.CLOSED
        )
        open_inquiry = Inquiry.objects.create(buyer=self.buyer, shop=self.shop)

        again = self._start()

        self.assertEqual(again.pk, open_inquiry.pk)
        self.assertEqual(Inquiry.objects.filter(status=Inquiry.Status.OPEN).count(), 1)

    def test_each_product_gets_its_own_conversation(self):
        about_a = self._start(product=self.product)
        about_b = self._start(product=self.second_product)
        general = self._start()

        self.assertEqual(len({about_a.pk, about_b.pk, general.pk}), 3)
        self.assertIsNone(general.product)

    def test_each_buyer_gets_their_own_conversation(self):
        mine = self._start(product=self.product)
        theirs = self._start(buyer=self.other_buyer, product=self.product)

        self.assertNotEqual(mine.pk, theirs.pk)

    def test_rejects_a_product_from_another_shop(self):
        with self.assertRaises(ValidationError):
            self._start(product=self.foreign_product)

        self.assertFalse(Inquiry.objects.exists())

    def test_rejects_a_draft_product(self):
        draft = self._product(self.shop, "Unpublished", status=ProductStatus.DRAFT)

        with self.assertRaises(ValidationError):
            self._start(product=draft)

    def test_rejects_an_owner_writing_to_their_own_shop(self):
        with self.assertRaises(ValidationError):
            self._start(buyer=self.owner)

    def test_invalid_messages_create_nothing_and_notify_nobody(self):
        cases = [
            ("   ", WEB),
            ("x" * 1001, WEB),
            (None, WEB),
            ("Hello", "sms"),
        ]
        for body, sent_via in cases:
            with self.subTest(body=body, sent_via=sent_via):
                with self.assertRaises(ValidationError):
                    start_inquiry(self.buyer, self.shop, body, sent_via)

        self.assertFalse(Inquiry.objects.exists())
        self.assertFalse(InquiryMessage.objects.exists())
        self.notify.assert_not_called()

    def test_accepts_exactly_the_maximum_length_and_trims_whitespace(self):
        inquiry = self._start(body="  " + "x" * 1000 + "\n")

        self.assertEqual(inquiry.messages.get().body, "x" * 1000)


class SendMessageTests(InquiryTestCase):
    def setUp(self):
        super().setUp()
        self.inquiry = self._start(product=self.product)
        self.notify.reset_mock()

    def test_shop_reply_is_recorded_as_shop_and_notifies_the_buyer(self):
        message = self._send(self.owner, self.inquiry.pk, "In stock.", TELEGRAM)

        self.inquiry.refresh_from_db()
        self.assertEqual(message.sender_role, InquiryMessage.SenderRole.SHOP)
        self.assertEqual(self.inquiry.last_sender_role, SenderRole.SHOP)
        self.assertEqual(message.sent_via, TELEGRAM)
        self.assertEqual(self.inquiry.buyer_unread, 1)
        self.assertEqual(self.inquiry.shop_unread, 1)
        self.notify.assert_called_once_with(
            self.buyer,
            "inquiry_reply",
            {
                "message_id": message.pk,
                "inquiry_id": self.inquiry.pk,
                "shop_name": "Test Shop",
                "product_name": "Phone A",
                "body": "In stock.",
            },
        )

    def test_buyer_follow_up_notifies_the_shop(self):
        message = self._send(self.buyer, self.inquiry.pk, "Still there?")

        self.inquiry.refresh_from_db()
        self.assertEqual(message.sender_role, InquiryMessage.SenderRole.BUYER)
        self.assertEqual(self.inquiry.last_sender_role, SenderRole.BUYER)
        self.assertEqual(self.inquiry.shop_unread, 2)
        self.notify.assert_called_once_with(
            self.owner,
            "inquiry_message",
            {
                "message_id": message.pk,
                "inquiry_id": self.inquiry.pk,
                "shop_id": self.shop.pk,
                "product_name": "Phone A",
                "body": "Still there?",
            },
        )

    def test_updates_last_message_at(self):
        before = Inquiry.objects.get(pk=self.inquiry.pk).last_message_at

        self._send(self.owner, self.inquiry.pk)

        self.inquiry.refresh_from_db()
        self.assertGreater(self.inquiry.last_message_at, before)

    def test_writing_to_a_closed_conversation_reopens_it(self):
        close_inquiry(self.owner, self.inquiry.pk)

        self._send(self.buyer, self.inquiry.pk)

        self.inquiry.refresh_from_db()
        self.assertEqual(self.inquiry.status, Inquiry.Status.OPEN)

    def test_cannot_reopen_when_a_newer_conversation_is_open(self):
        close_inquiry(self.buyer, self.inquiry.pk)
        Inquiry.objects.create(buyer=self.buyer, shop=self.shop, product=self.product)

        with self.assertRaises(ValidationError):
            self._send(self.buyer, self.inquiry.pk)

    def test_invalid_body_changes_nothing_and_notifies_nobody(self):
        with self.assertRaises(ValidationError):
            self._send(self.buyer, self.inquiry.pk, "x" * 1001)

        self.inquiry.refresh_from_db()
        self.assertEqual(self.inquiry.shop_unread, 1)
        self.assertEqual(self.inquiry.messages.count(), 1)
        self.notify.assert_not_called()


class MarkReadAndCloseTests(InquiryTestCase):
    def setUp(self):
        super().setUp()
        self.inquiry = Inquiry.objects.create(
            buyer=self.buyer, shop=self.shop, buyer_unread=2, shop_unread=3
        )

    def test_buyer_clears_only_the_buyer_counter(self):
        mark_read(self.buyer, self.inquiry.pk)

        self.inquiry.refresh_from_db()
        self.assertEqual(self.inquiry.buyer_unread, 0)
        self.assertEqual(self.inquiry.shop_unread, 3)

    def test_owner_clears_only_the_shop_counter(self):
        mark_read(self.owner, self.inquiry.pk)

        self.inquiry.refresh_from_db()
        self.assertEqual(self.inquiry.buyer_unread, 2)
        self.assertEqual(self.inquiry.shop_unread, 0)

    def test_either_side_can_close(self):
        for user in (self.buyer, self.owner):
            with self.subTest(user=user.username):
                Inquiry.objects.filter(pk=self.inquiry.pk).update(
                    status=Inquiry.Status.OPEN
                )

                close_inquiry(user, self.inquiry.pk)

                self.inquiry.refresh_from_db()
                self.assertEqual(self.inquiry.status, Inquiry.Status.CLOSED)


class ConstraintTests(InquiryTestCase):
    def test_database_allows_only_one_open_inquiry_per_product(self):
        Inquiry.objects.create(buyer=self.buyer, shop=self.shop, product=self.product)

        with self.assertRaises(IntegrityError), transaction.atomic():
            Inquiry.objects.create(
                buyer=self.buyer, shop=self.shop, product=self.product
            )

    def test_database_allows_only_one_open_general_inquiry(self):
        Inquiry.objects.create(buyer=self.buyer, shop=self.shop)

        with self.assertRaises(IntegrityError), transaction.atomic():
            Inquiry.objects.create(buyer=self.buyer, shop=self.shop)

    def test_closed_inquiries_do_not_count(self):
        Inquiry.objects.create(
            buyer=self.buyer, shop=self.shop, status=Inquiry.Status.CLOSED
        )
        Inquiry.objects.create(buyer=self.buyer, shop=self.shop)


class ProductDeletionTests(InquiryTestCase):
    def test_conversation_survives_the_product_being_deleted(self):
        inquiry = self._start(product=self.product)

        self.product.delete()

        inquiry.refresh_from_db()
        self.assertIsNone(inquiry.product)
        self.assertEqual(inquiry.status, Inquiry.Status.OPEN)
        self.assertEqual(inquiry.messages.count(), 1)

    def test_closes_the_product_conversation_if_it_would_collide(self):
        general = self._start()
        about_product = self._start(product=self.product)

        self.product.delete()

        about_product.refresh_from_db()
        general.refresh_from_db()
        self.assertEqual(about_product.status, Inquiry.Status.CLOSED)
        self.assertEqual(general.status, Inquiry.Status.OPEN)

    def test_deleting_several_products_at_once_does_not_collide(self):
        about_a = self._start(product=self.product)
        about_b = self._start(product=self.second_product)

        Product.objects.filter(
            pk__in=[self.product.pk, self.second_product.pk]
        ).delete()

        statuses = sorted(
            Inquiry.objects.filter(pk__in=[about_a.pk, about_b.pk]).values_list(
                "status", flat=True
            )
        )
        self.assertEqual(statuses, [Inquiry.Status.CLOSED, Inquiry.Status.OPEN])


class IsolationTests(InquiryTestCase):
    """A foreign inquiry must be indistinguishable from a missing one."""

    def setUp(self):
        super().setUp()
        self.mine = self._start(product=self.product)
        self.theirs_same_shop = self._start(buyer=self.other_buyer)
        self.other_shops = self._start(shop=self.other_shop)
        self.missing_pk = Inquiry.objects.order_by("-pk").first().pk + 1000

    def test_buyer_lists_only_their_own_inquiries(self):
        self.assertCountEqual(
            selectors.inquiries_for_buyer(self.buyer),
            [self.mine, self.other_shops],
        )
        self.assertCountEqual(
            selectors.inquiries_for_buyer(self.other_buyer), [self.theirs_same_shop]
        )

    def test_shop_lists_only_its_own_inquiries(self):
        self.assertCountEqual(
            selectors.inquiries_for_shop(self.shop),
            [self.mine, self.theirs_same_shop],
        )
        self.assertCountEqual(
            selectors.inquiries_for_shop(self.other_shop), [self.other_shops]
        )
        self.assertCountEqual(
            selectors.inquiries_for_owner(self.other_owner), [self.other_shops]
        )

    def test_cross_buyer_lookup_is_not_found(self):
        for pk in (self.mine.pk, self.missing_pk):
            with self.subTest(pk=pk), self.assertRaises(Inquiry.DoesNotExist):
                selectors.get_inquiry_for_buyer(self.other_buyer, pk)

    def test_cross_shop_lookup_is_not_found(self):
        for pk in (self.mine.pk, self.missing_pk):
            with self.subTest(pk=pk), self.assertRaises(Inquiry.DoesNotExist):
                selectors.get_inquiry_for_shop(self.other_shop, pk)

    def test_strangers_cannot_read_the_thread(self):
        for user in (self.other_buyer, self.other_owner):
            with self.subTest(user=user.username):
                with self.assertRaises(Inquiry.DoesNotExist):
                    selectors.messages_for_participant(user, self.mine.pk)

    def test_cross_buyer_and_cross_shop_writes_are_not_found(self):
        strangers = (self.other_buyer, self.other_owner)
        actions = {
            "send_message": lambda user, pk: send_message(user, pk, "Hi", WEB),
            "mark_read": mark_read,
            "close_inquiry": close_inquiry,
        }
        for user in strangers:
            for name, action in actions.items():
                for pk in (self.mine.pk, self.missing_pk):
                    with self.subTest(user=user.username, action=name, pk=pk):
                        with self.assertRaises(Inquiry.DoesNotExist):
                            action(user, pk)

        self.mine.refresh_from_db()
        self.assertEqual(self.mine.status, Inquiry.Status.OPEN)
        self.assertEqual(self.mine.shop_unread, 1)
        self.assertEqual(self.mine.messages.count(), 1)

    def test_owner_of_one_shop_is_a_buyer_elsewhere(self):
        inquiry = self._start(buyer=self.owner, shop=self.other_shop)

        message = self._send(self.owner, inquiry.pk)

        self.assertEqual(message.sender_role, InquiryMessage.SenderRole.BUYER)
        with self.assertRaises(Inquiry.DoesNotExist):
            selectors.get_inquiry_for_shop(self.shop, inquiry.pk)


class RealNotificationEndToEndTests(TestCase):
    def setUp(self):
        self.buyer = User.objects.create_user(username="buyer_e2e", password="pass")
        self.owner = User.objects.create_user(username="owner_e2e", password="pass")
        self.shop = Shop.objects.create(
            name="E2E Shop",
            owner=self.owner,
            telegram_chat_id="998877",
        )
        self.category = Category.objects.create(name="Phones", slug="phones-e2e")
        self.product = Product.objects.create(
            shop=self.shop,
            category=self.category,
            brand="Acme",
            name="Phone E2E",
            price="1500.00",
        )

    def test_buyer_message_to_shop_with_telegram_chat_id_creates_notification_and_outbox(
        self,
    ):
        inquiry = start_inquiry(
            self.buyer,
            self.shop,
            "Is Phone E2E in stock?",
            WEB,
            product=self.product,
        )

        notif = Notification.objects.get(recipient=self.owner, kind="inquiry_message")
        self.assertEqual(notif.payload["product_name"], "Phone E2E")
        self.assertEqual(notif.payload["body"], "Is Phone E2E in stock?")
        self.assertEqual(notif.payload["inquiry_id"], inquiry.pk)

        outbox = OutboundMessage.objects.get(chat_id="998877")
        self.assertTrue(outbox.send_silently_at_night)
        self.assertEqual(outbox.kind, "notify:inquiry_message")
        self.assertIn("Phone E2E", outbox.payload["text"])
        self.assertIn("Is Phone E2E in stock?", outbox.payload["text"])

    def test_no_notification_if_the_transaction_rolls_back(self):
        # Real notify, not a mock: the point is that the notification and its
        # Telegram outbox row share the message's transaction.
        inquiry = start_inquiry(
            self.buyer, self.shop, "Hello", WEB, product=self.product
        )
        notifications_before = Notification.objects.count()
        outbox_before = OutboundMessage.objects.count()

        with self.assertRaises(RuntimeError), transaction.atomic():
            send_message(self.buyer, inquiry.pk, "Still there?", WEB)
            raise RuntimeError("simulated error")

        self.assertEqual(Notification.objects.count(), notifications_before)
        self.assertEqual(OutboundMessage.objects.count(), outbox_before)

    def test_shop_reply_creates_inquiry_reply_notification_for_buyer(self):
        inquiry = start_inquiry(
            self.buyer,
            self.shop,
            "Hello",
            WEB,
            product=self.product,
        )
        Notification.objects.all().delete()

        reply = send_message(self.owner, inquiry.pk, "Yes, in stock!", WEB)

        notif = Notification.objects.get(recipient=self.buyer, kind="inquiry_reply")
        self.assertEqual(notif.payload["inquiry_id"], inquiry.pk)
        self.assertEqual(notif.payload["message_id"], reply.pk)
        self.assertEqual(notif.payload["shop_name"], "E2E Shop")
        self.assertEqual(notif.payload["product_name"], "Phone E2E")
        self.assertEqual(notif.payload["body"], "Yes, in stock!")


class RemindUnansweredInquiriesTests(TestCase):
    def setUp(self):
        self.buyer = User.objects.create_user(username="buyer_rem", password="pass")
        self.owner = User.objects.create_user(username="owner_rem", password="pass")
        self.shop = Shop.objects.create(
            name="Reminder Shop",
            owner=self.owner,
            telegram_chat_id="554433",
        )
        self.category = Category.objects.create(name="Shoes", slug="shoes-rem")
        self.product = Product.objects.create(
            shop=self.shop,
            category=self.category,
            brand="Acme",
            name="Running Shoes",
            price="2000.00",
        )
        self.now = timezone.now()

    def _create_inquiry(self, **kwargs):
        defaults = {
            "buyer": self.buyer,
            "shop": self.shop,
            "product": self.product,
            "status": Inquiry.Status.OPEN,
            "last_sender_role": SenderRole.BUYER,
            "last_message_at": self.now - timedelta(hours=25),
        }
        defaults.update(kwargs)
        return Inquiry.objects.create(**defaults)

    def test_buyer_wrote_25h_ago_creates_one_reminder_and_returns_one(self):
        inquiry = self._create_inquiry()

        created_count = remind_unanswered_inquiries(now=self.now)

        self.assertEqual(created_count, 1)
        notif = Notification.objects.get(
            recipient=self.owner, kind="inquiry_unanswered"
        )
        self.assertEqual(notif.payload["inquiry_id"], inquiry.pk)
        self.assertEqual(notif.payload["shop_id"], self.shop.pk)
        self.assertEqual(notif.payload["product_name"], "Running Shoes")
        self.assertEqual(notif.dedupe_key, f"inquiry_unanswered:{inquiry.pk}")

    def test_running_again_creates_no_second_reminder_and_returns_zero(self):
        self._create_inquiry()
        self.assertEqual(remind_unanswered_inquiries(now=self.now), 1)

        # Second run immediately
        self.assertEqual(remind_unanswered_inquiries(now=self.now), 0)
        self.assertEqual(
            Notification.objects.filter(kind="inquiry_unanswered").count(), 1
        )

    def test_shop_wrote_last_creates_no_reminder(self):
        self._create_inquiry(last_sender_role=SenderRole.SHOP)

        created_count = remind_unanswered_inquiries(now=self.now)

        self.assertEqual(created_count, 0)
        self.assertFalse(
            Notification.objects.filter(kind="inquiry_unanswered").exists()
        )

    def test_buyer_wrote_2h_ago_creates_no_reminder(self):
        self._create_inquiry(last_message_at=self.now - timedelta(hours=2))

        created_count = remind_unanswered_inquiries(now=self.now)

        self.assertEqual(created_count, 0)
        self.assertFalse(
            Notification.objects.filter(kind="inquiry_unanswered").exists()
        )

    def test_buyer_wrote_8_days_ago_creates_no_reminder(self):
        self._create_inquiry(last_message_at=self.now - timedelta(days=8))

        created_count = remind_unanswered_inquiries(now=self.now)

        self.assertEqual(created_count, 0)
        self.assertFalse(
            Notification.objects.filter(kind="inquiry_unanswered").exists()
        )

    def test_closed_inquiry_creates_no_reminder(self):
        self._create_inquiry(status=Inquiry.Status.CLOSED)

        created_count = remind_unanswered_inquiries(now=self.now)

        self.assertEqual(created_count, 0)
        self.assertFalse(
            Notification.objects.filter(kind="inquiry_unanswered").exists()
        )

    def test_blank_last_sender_role_creates_no_reminder(self):
        self._create_inquiry(last_sender_role="")

        created_count = remind_unanswered_inquiries(now=self.now)

        self.assertEqual(created_count, 0)
        self.assertFalse(
            Notification.objects.filter(kind="inquiry_unanswered").exists()
        )

    def test_query_count_is_bounded_and_does_not_grow_with_candidates(self):
        # Create 3 distinct inquiries that are already reminded
        inquiries = []
        for i in range(3):
            buyer = User.objects.create_user(username=f"buyer_qc_{i}", password="pass")
            inq = Inquiry.objects.create(
                buyer=buyer,
                shop=self.shop,
                status=Inquiry.Status.OPEN,
                last_sender_role=SenderRole.BUYER,
                last_message_at=self.now - timedelta(hours=25),
            )
            Notification.objects.create(
                recipient=self.owner,
                kind="inquiry_unanswered",
                payload={"inquiry_id": inq.pk, "shop_id": self.shop.pk},
                link=f"/shop/dashboard/inquiries/{inq.pk}",
                dedupe_key=f"inquiry_unanswered:{inq.pk}",
            )
            inquiries.append(inq)

        # When checking already-reminded candidates, remind_unanswered_inquiries
        # issues exactly 2 queries (1 to fetch candidates with select_related,
        # 1 bulk query to fetch existing dedupe keys). It does NOT query per candidate.
        with self.assertNumQueries(2):
            result = remind_unanswered_inquiries(now=self.now)
            self.assertEqual(result, 0)

        # Now delete 2 of the inquiries and verify the query count with 1 candidate
        # is still exactly 2 queries (constant bound).
        Inquiry.objects.filter(pk__in=[inquiries[1].pk, inquiries[2].pk]).delete()
        Notification.objects.filter(
            dedupe_key__in=[
                f"inquiry_unanswered:{inquiries[1].pk}",
                f"inquiry_unanswered:{inquiries[2].pk}",
            ]
        ).delete()

        with self.assertNumQueries(2):
            result = remind_unanswered_inquiries(now=self.now)
            self.assertEqual(result, 0)


class InquirySerializerTests(InquiryTestCase):
    def test_inquiry_serializer_returns_nested_shop_product_and_messages(self):
        inquiry = self._start(product=self.product, body="Hello")

        data = InquirySerializer(instance=inquiry).data

        self.assertEqual(data["shop"]["id"], self.shop.pk)
        self.assertEqual(data["product"]["id"], self.product.pk)
        self.assertEqual(data["messages"][0]["body"], "Hello")
        self.assertEqual(data["messages"][0]["sender_role"], SenderRole.BUYER)

    def test_inquiry_serializer_returns_null_for_missing_product(self):
        inquiry = self._start()

        self.assertIsNone(InquirySerializer(instance=inquiry).data["product"])

    def test_inquiry_response_fields_are_read_only(self):
        serializer = InquirySerializer(
            data={
                "buyer": self.other_buyer.pk,
                "shop": self.shop.pk,
                "status": Inquiry.Status.CLOSED,
                "buyer_unread": 900,
                "shop_unread": 900,
            }
        )

        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.assertEqual(serializer.validated_data, {})

    def test_message_serializer_includes_message_fields(self):
        inquiry = self._start(body="Question")
        message = inquiry.messages.get()

        data = InquiryMessageSerializer(instance=message).data

        self.assertEqual(data["inquiry"], inquiry.pk)
        self.assertEqual(data["body"], "Question")
        self.assertEqual(data["sent_via"], WEB)
        self.assertEqual(data["sender_role"], SenderRole.BUYER)
        self.assertIn("created_at", data)

    def test_buyer_request_serializer_resolves_shop_and_optional_product(self):
        serializer = BuyerInquiryRequestSerializer(
            data={
                "shop": self.shop.pk,
                "product": self.product.pk,
                "body": "Question",
                "sent_via": WEB,
            }
        )

        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.assertEqual(serializer.validated_data["shop"], self.shop)
        self.assertEqual(serializer.validated_data["product"], self.product)

    def test_buyer_request_serializer_requires_a_body_and_ignores_sent_via(self):
        serializer = BuyerInquiryRequestSerializer(
            data={"shop": self.shop.pk, "body": "", "sent_via": "sms"}
        )

        self.assertFalse(serializer.is_valid())
        self.assertIn("body", serializer.errors)
        self.assertNotIn("sent_via", serializer.errors)

    def test_buyer_request_serializer_rejects_unknown_shop_and_product(self):
        serializer = BuyerInquiryRequestSerializer(
            data={
                "shop": 999999,
                "product": 999999,
                "body": "Question",
                "sent_via": WEB,
            }
        )

        self.assertFalse(serializer.is_valid())
        self.assertIn("shop", serializer.errors)
        self.assertIn("product", serializer.errors)

    def test_reply_serializer_validates_body_and_takes_no_sent_via(self):
        serializer = InquiryReplySerializer(data={"body": "Hello"})
        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.assertEqual(serializer.validated_data["body"], "Hello")
        self.assertNotIn("sent_via", serializer.validated_data)


class InquiryViewTests(InquiryTestCase):
    def setUp(self):
        super().setUp()
        self.client = APIClient()

    def test_buyer_endpoints_require_authentication(self):
        self.assertEqual(self.client.get(reverse("buyer-list")).status_code, 401)
        response = self.client.post(
            reverse("buyer-list"),
            {"shop": self.shop.pk, "body": "Question", "sent_via": WEB},
            format="json",
        )
        self.assertEqual(response.status_code, 401)

    def test_buyer_create_returns_serialized_inquiry(self):
        self.client.force_authenticate(self.buyer)

        response = self.client.post(
            reverse("buyer-list"),
            {
                "shop": self.shop.pk,
                "product": self.product.pk,
                "body": "Is this in stock?",
                "sent_via": WEB,
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["buyer"], self.buyer.pk)
        self.assertEqual(response.data["shop"]["id"], self.shop.pk)
        self.assertEqual(response.data["product"]["id"], self.product.pk)
        self.assertEqual(response.data["messages"][0]["body"], "Is this in stock?")

    def test_buyer_create_returns_validation_errors_for_bad_input(self):
        self.client.force_authenticate(self.buyer)

        response = self.client.post(
            reverse("buyer-list"),
            {"shop": self.shop.pk, "body": ""},
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("body", response.data)
        self.assertFalse(Inquiry.objects.exists())

    def test_buyer_list_is_scoped_to_authenticated_buyer(self):
        mine = self._start()
        theirs = self._start(buyer=self.other_buyer)
        self.client.force_authenticate(self.buyer)

        response = self.client.get(reverse("buyer-list"))

        self.assertEqual(response.status_code, 200)
        results = response.data["results"]
        self.assertEqual([row["id"] for row in results], [mine.pk])
        self.assertNotIn(theirs.pk, [row["id"] for row in results])

    def test_buyer_retrieve_marks_only_buyer_messages_read(self):
        inquiry = self._start()
        Inquiry.objects.filter(pk=inquiry.pk).update(buyer_unread=2, shop_unread=3)
        self.client.force_authenticate(self.buyer)

        response = self.client.get(reverse("buyer-detail", args=[inquiry.pk]))

        self.assertEqual(response.status_code, 200)
        inquiry.refresh_from_db()
        self.assertEqual(inquiry.buyer_unread, 0)
        self.assertEqual(inquiry.shop_unread, 3)

    def test_buyer_cannot_retrieve_another_buyers_inquiry(self):
        inquiry = self._start()
        self.client.force_authenticate(self.other_buyer)

        response = self.client.get(reverse("buyer-detail", args=[inquiry.pk]))

        self.assertEqual(response.status_code, 404)

    def test_buyer_reply_appends_message_and_increments_shop_unread(self):
        inquiry = self._start()
        self.client.force_authenticate(self.buyer)

        response = self.client.post(
            reverse("buyer-reply", args=[inquiry.pk]),
            {"body": "Can I pick it up today?"},
            format="json",
        )

        self.assertEqual(response.status_code, 200, response.data)
        inquiry.refresh_from_db()
        self.assertEqual(inquiry.shop_unread, 2)
        self.assertEqual(inquiry.last_sender_role, SenderRole.BUYER)
        self.assertEqual(inquiry.messages.count(), 2)
        self.assertEqual(inquiry.messages.last().body, "Can I pick it up today?")

    def test_buyer_can_close_their_inquiry(self):
        inquiry = self._start()
        self.client.force_authenticate(self.buyer)

        response = self.client.post(
            reverse("buyer-close", args=[inquiry.pk]), format="json"
        )

        self.assertEqual(response.status_code, 200)
        inquiry.refresh_from_db()
        self.assertEqual(inquiry.status, Inquiry.Status.CLOSED)

    def test_buyer_unreads_returns_count(self):
        inquiry = self._start()
        Inquiry.objects.filter(pk=inquiry.pk).update(buyer_unread=4)
        self.client.force_authenticate(self.buyer)

        response = self.client.get(reverse("buyer-unreads"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, {"unread_count": 4})

    def test_shop_list_is_scoped_to_owned_shop(self):
        mine = self._start()
        theirs = self._start(shop=self.other_shop)
        self.client.force_authenticate(self.owner)

        response = self.client.get(reverse("shop-list"))

        self.assertEqual(response.status_code, 200)
        results = response.data["results"]
        self.assertEqual([row["id"] for row in results], [mine.pk])
        self.assertNotIn(theirs.pk, [row["id"] for row in results])

    def test_shop_cannot_retrieve_another_shops_inquiry(self):
        inquiry = self._start(shop=self.other_shop)
        self.client.force_authenticate(self.owner)

        response = self.client.get(reverse("shop-detail", args=[inquiry.pk]))
        self.assertEqual(response.status_code, 404)

    def test_buyer_cannot_retrieve_inquiry_via_shop_endpoint(self):
        inquiry = self._start()
        self.client.force_authenticate(self.buyer)

        response = self.client.get(reverse("shop-detail", args=[inquiry.pk]))
        self.assertEqual(response.status_code, 404)

    def test_shop_reply_appends_message_and_increments_buyer_unread(self):
        inquiry = self._start()
        self.client.force_authenticate(self.owner)

        response = self.client.post(
            reverse("shop-reply", args=[inquiry.pk]),
            {"body": "Yes, Phone A is currently in stock!"},
            format="json",
        )

        self.assertEqual(response.status_code, 200, response.data)
        inquiry.refresh_from_db()
        self.assertEqual(inquiry.buyer_unread, 1)
        self.assertEqual(inquiry.last_sender_role, SenderRole.SHOP)
        self.assertEqual(inquiry.messages.count(), 2)
        self.assertEqual(
            inquiry.messages.last().body, "Yes, Phone A is currently in stock!"
        )
        self.assertEqual(inquiry.messages.last().sender_role, SenderRole.SHOP)

    def test_shop_list_filters_by_open_and_unread(self):
        inquiry1 = self._start()  # open, shop_unread=1
        inquiry2 = self._start(product=self.second_product)
        close_inquiry(self.owner, inquiry2.pk)
        Inquiry.objects.filter(pk=inquiry2.pk).update(shop_unread=0)

        self.client.force_authenticate(self.owner)

        # Filter open=true
        resp = self.client.get(reverse("shop-list"), {"open": "true"})
        self.assertEqual(resp.status_code, 200)
        ids = [r["id"] for r in resp.data["results"]]
        self.assertIn(inquiry1.pk, ids)
        self.assertNotIn(inquiry2.pk, ids)

        # Filter open=false
        resp = self.client.get(reverse("shop-list"), {"open": "false"})
        self.assertEqual(resp.status_code, 200)
        ids = [r["id"] for r in resp.data["results"]]
        self.assertNotIn(inquiry1.pk, ids)
        self.assertIn(inquiry2.pk, ids)

        # Filter unread=true
        resp = self.client.get(reverse("shop-list"), {"unread": "true"})
        self.assertEqual(resp.status_code, 200)
        ids = [r["id"] for r in resp.data["results"]]
        self.assertIn(inquiry1.pk, ids)
        self.assertNotIn(inquiry2.pk, ids)

    def test_inquiries_cannot_be_deleted(self):
        inquiry = self._start()
        self.client.force_authenticate(self.buyer)
        self.assertEqual(
            self.client.delete(reverse("buyer-detail", args=[inquiry.pk])).status_code,
            405,
        )
        self.client.force_authenticate(self.owner)
        self.assertEqual(
            self.client.delete(reverse("shop-detail", args=[inquiry.pk])).status_code,
            405,
        )

    def test_shop_unreads_returns_count(self):
        inquiry = self._start()
        Inquiry.objects.filter(pk=inquiry.pk).update(shop_unread=5)
        self.client.force_authenticate(self.owner)

        response = self.client.get(reverse("shop-unreads"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, {"unread_count": 5})


class ContactIntentTests(InquiryTestCase):
    def setUp(self):
        super().setUp()
        self.shop.phone = "+251911223344"
        self.shop.telegram_username = "test_shop_tg"
        self.shop.save()

    def test_phone_contact_requires_authentication(self):
        client = APIClient()
        response = client.post(
            reverse("contact-intent"),
            {"shop": self.shop.pk, "channel": "phone"},
            format="json",
        )
        self.assertEqual(response.status_code, 403)
        self.assertFalse(ContactIntent.objects.exists())

    def test_phone_contact_succeeds_for_authenticated_buyer(self):
        client = APIClient()
        client.force_authenticate(self.buyer)

        response = client.post(
            reverse("contact-intent"),
            {
                "shop": self.shop.pk,
                "product": self.product.pk,
                "channel": "phone",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["channel"], "phone")
        self.assertEqual(response.data["target"], "tel:+251911223344")
        self.assertEqual(response.data["phone"], "+251911223344")

        intent = ContactIntent.objects.get()
        self.assertEqual(intent.shop, self.shop)
        self.assertEqual(intent.product, self.product)
        self.assertEqual(intent.user, self.buyer)
        self.assertEqual(intent.channel, "phone")

    def test_telegram_contact_returns_deep_link(self):
        client = APIClient()
        response = client.post(
            reverse("contact-intent"),
            {"shop": self.shop.pk, "channel": "telegram"},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["channel"], "telegram")
        self.assertEqual(response.data["target"], "https://t.me/test_shop_tg")
        self.assertEqual(response.data["telegram_url"], "https://t.me/test_shop_tg")

        intent = ContactIntent.objects.get()
        self.assertEqual(intent.channel, "telegram")

    def test_honeypot_rejects_bots(self):
        client = APIClient()
        client.force_authenticate(self.buyer)
        response = client.post(
            reverse("contact-intent"),
            {"shop": self.shop.pk, "channel": "phone", "honeypot": "spam-bot"},
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(ContactIntent.objects.exists())

    def test_blocked_user_agent_rejects_automated_crawlers(self):
        client = APIClient()
        client.force_authenticate(self.buyer)
        response = client.post(
            reverse("contact-intent"),
            {"shop": self.shop.pk, "channel": "phone"},
            HTTP_USER_AGENT="python-requests/2.28.1",
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(ContactIntent.objects.exists())

    def test_foreign_product_validation(self):
        client = APIClient()
        client.force_authenticate(self.buyer)
        response = client.post(
            reverse("contact-intent"),
            {
                "shop": self.shop.pk,
                "product": self.foreign_product.pk,
                "channel": "phone",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("product", response.data)

    def test_shop_missing_phone_or_telegram(self):
        empty_shop = Shop.objects.create(name="Empty Shop", owner=self.other_owner)
        client = APIClient()
        client.force_authenticate(self.buyer)

        resp_phone = client.post(
            reverse("contact-intent"),
            {"shop": empty_shop.pk, "channel": "phone"},
            format="json",
        )
        self.assertEqual(resp_phone.status_code, 400)

        resp_tg = client.post(
            reverse("contact-intent"),
            {"shop": empty_shop.pk, "channel": "telegram"},
            format="json",
        )
        self.assertEqual(resp_tg.status_code, 400)

    def test_telegram_username_sanitization_on_shop_save(self):
        shop1 = Shop.objects.create(
            name="Handle Shop",
            owner=self.owner,
            telegram_username="@my_handle",
        )
        self.assertEqual(shop1.telegram_username, "my_handle")

        shop2 = Shop.objects.create(
            name="URL Shop",
            owner=self.owner,
            telegram_username="https://t.me/url_handle/",
        )
        self.assertEqual(shop2.telegram_username, "url_handle")

    def test_shop_public_serializer_phone_gated_by_authentication(self):
        from rest_framework.test import APIRequestFactory

        from shops.serializers import ShopPublicSerializer

        factory = APIRequestFactory()

        # Anonymous request
        anon_req = factory.get("/")
        anon_data = ShopPublicSerializer(self.shop, context={"request": anon_req}).data
        self.assertEqual(anon_data["phone"], "")
        self.assertEqual(anon_data["telegram_username"], "test_shop_tg")

        # Authenticated request
        auth_req = factory.get("/")
        auth_req.user = self.buyer
        auth_data = ShopPublicSerializer(self.shop, context={"request": auth_req}).data
        self.assertEqual(auth_data["phone"], "+251911223344")

    def test_contact_intent_selectors(self):
        from inquiries.selectors import (
            contact_intents_count_for_product,
            contact_intents_count_for_shop,
        )

        record_contact_intent(self.shop, "telegram", user=None, product=self.product)
        record_contact_intent(self.shop, "phone", user=self.buyer, product=self.product)

        self.assertEqual(contact_intents_count_for_shop(self.shop), 2)
        self.assertEqual(contact_intents_count_for_product(self.product), 2)
        self.assertEqual(contact_intents_count_for_product(self.second_product), 0)

    def test_contact_intent_accepts_slugs_and_infers_shop(self):
        client = APIClient()
        client.force_authenticate(self.buyer)
        # Using shop slug
        resp = client.post(
            reverse("contact-intent"),
            {"shop": self.shop.slug, "channel": "telegram"},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)

        # Using product slug, omitting shop (infers shop from product)
        resp2 = client.post(
            reverse("contact-intent"),
            {"product": self.product.slug, "channel": "telegram"},
            format="json",
        )
        self.assertEqual(resp2.status_code, 200)
        self.assertEqual(resp2.data["target"], "https://t.me/test_shop_tg")


class InquiryApiHardeningTests(InquiryTestCase):
    def setUp(self):
        super().setUp()
        self.client = APIClient()
        # Throttle counters live in the cache and outlive a test's transaction.
        cache.clear()
        self.addCleanup(cache.clear)

    def test_shop_sees_the_buyer_as_an_id_and_none_of_their_private_fields(self):
        User.objects.filter(pk=self.buyer.pk).update(
            email="buyer@example.com", phone="+251900000001", telegram_chat_id="4242"
        )
        self._start(product=self.product)
        self.client.force_authenticate(self.owner)

        response = self.client.get(reverse("shop-list"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["results"][0]["buyer"], self.buyer.pk)
        body = response.content.decode()
        for private in ("buyer@example.com", "+251900000001", "4242"):
            self.assertNotIn(private, body)

    def test_inbox_query_count_does_not_grow_with_rows(self):
        self.client.force_authenticate(self.buyer)
        self._start(product=self.product)
        with CaptureQueriesContext(connection) as one_row:
            self.assertEqual(self.client.get(reverse("buyer-list")).status_code, 200)

        # Different shops and products, so no row is served from another's cache.
        for i in range(4):
            shop = Shop.objects.create(name=f"Shop {i}", owner=self.other_owner)
            self._start(shop=shop, product=self._product(shop, f"Item {i}"))
        with CaptureQueriesContext(connection) as five_rows:
            self.assertEqual(self.client.get(reverse("buyer-list")).status_code, 200)

        self.assertEqual(len(one_row), len(five_rows))

    def test_non_numeric_id_is_a_404(self):
        self.client.force_authenticate(self.buyer)
        for url in (reverse("buyer-list") + "abc/", reverse("shop-list") + "abc/"):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 404)

    def test_client_cannot_claim_a_telegram_message(self):
        self.client.force_authenticate(self.buyer)

        response = self.client.post(
            reverse("buyer-list"),
            {"shop": self.shop.pk, "body": "Hi", "sent_via": TELEGRAM},
            format="json",
        )

        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(InquiryMessage.objects.get().sent_via, WEB)

    def test_a_follow_up_is_not_a_new_lead_but_a_restart_is(self):
        self.client.force_authenticate(self.buyer)
        payload = {"shop": self.shop.pk, "product": self.product.pk, "body": "Hi"}

        self.client.post(reverse("buyer-list"), payload, format="json")
        self.client.post(reverse("buyer-list"), payload, format="json")
        leads = ContactIntent.objects.filter(channel=ContactChannel.IN_APP)
        self.assertEqual(leads.count(), 1)

        close_inquiry(self.buyer, Inquiry.objects.get().pk)
        self.client.post(reverse("buyer-list"), payload, format="json")
        self.assertEqual(leads.count(), 2)

    def test_sending_is_throttled_per_user(self):
        inquiry = self._start()
        self.client.force_authenticate(self.buyer)
        url = reverse("buyer-reply", args=[inquiry.pk])

        for _ in range(20):
            self.assertEqual(
                self.client.post(url, {"body": "Hi"}, format="json").status_code, 200
            )
        self.assertEqual(
            self.client.post(url, {"body": "Hi"}, format="json").status_code, 429
        )

    def test_opening_an_already_read_thread_writes_nothing(self):
        inquiry = Inquiry.objects.create(buyer=self.buyer, shop=self.shop)

        with CaptureQueriesContext(connection) as ctx:
            mark_read(self.buyer, inquiry.pk)

        self.assertFalse(
            any(q["sql"].lstrip().upper().startswith("UPDATE") for q in ctx)
        )


@override_settings(REST_FRAMEWORK={**settings.REST_FRAMEWORK, "NUM_PROXIES": 0})
class ContactIntentHardeningTests(InquiryTestCase):
    def setUp(self):
        super().setUp()
        self.shop.telegram_username = "test_shop_tg"
        self.shop.save()
        self.url = reverse("contact-intent")
        cache.clear()
        self.addCleanup(cache.clear)

    def test_anonymous_callers_are_rate_limited(self):
        client = APIClient()
        payload = {"shop": self.shop.pk, "channel": "telegram"}

        for _ in range(30):
            self.assertEqual(
                client.post(self.url, payload, format="json").status_code, 200
            )
        self.assertEqual(client.post(self.url, payload, format="json").status_code, 429)

    def test_a_json_array_body_is_a_400_not_a_500(self):
        client = APIClient()
        client.force_authenticate(self.buyer)

        self.assertEqual(client.post(self.url, [], format="json").status_code, 400)

    def test_forwarded_for_header_cannot_choose_the_ip_hash(self):
        APIClient().post(
            self.url,
            {"shop": self.shop.pk, "channel": "telegram"},
            format="json",
            HTTP_X_FORWARDED_FOR="203.0.113.9",
            REMOTE_ADDR="198.51.100.7",
        )

        self.assertEqual(ContactIntent.objects.get().ip_hash, _hash_ip("198.51.100.7"))

    def test_ip_hash_is_keyed_not_a_bare_sha256(self):
        self.assertNotEqual(
            _hash_ip("198.51.100.7"), hashlib.sha256(b"198.51.100.7").hexdigest()
        )
