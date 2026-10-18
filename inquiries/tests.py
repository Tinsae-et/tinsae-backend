from unittest import mock

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase

from catalog.models import Category, Product, ProductStatus
from inquiries import selectors
from inquiries.models import Inquiry, InquiryMessage
from inquiries.services import (
    NEW_MESSAGE,
    close_inquiry,
    mark_read,
    send_message,
    start_inquiry,
)
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
        with self.captureOnCommitCallbacks(execute=True):
            return start_inquiry(
                buyer or self.buyer, shop or self.shop, body, WEB, **kwargs
            )

    def _send(self, user, inquiry_id, body="Hello", sent_via=WEB):
        with self.captureOnCommitCallbacks(execute=True):
            return send_message(user, inquiry_id, body, sent_via)


class StartInquiryTests(InquiryTestCase):
    def test_creates_inquiry_with_first_buyer_message(self):
        inquiry = self._start(product=self.product)

        inquiry.refresh_from_db()
        message = inquiry.messages.get()
        self.assertEqual(message.sender_role, InquiryMessage.SenderRole.BUYER)
        self.assertEqual(message.sent_via, WEB)
        self.assertEqual(inquiry.product, self.product)
        self.assertEqual(inquiry.status, Inquiry.Status.OPEN)
        self.assertEqual(inquiry.shop_unread, 1)
        self.assertEqual(inquiry.buyer_unread, 0)

    def test_notifies_the_shop(self):
        inquiry = self._start()

        self.notify.assert_called_once_with(
            self.shop, NEW_MESSAGE, inquiry=inquiry, message=inquiry.messages.get()
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
        self.assertEqual(message.sent_via, TELEGRAM)
        self.assertEqual(self.inquiry.buyer_unread, 1)
        self.assertEqual(self.inquiry.shop_unread, 1)
        self.notify.assert_called_once_with(
            self.buyer, NEW_MESSAGE, inquiry=mock.ANY, message=message
        )

    def test_buyer_follow_up_notifies_the_shop(self):
        message = self._send(self.buyer, self.inquiry.pk)

        self.inquiry.refresh_from_db()
        self.assertEqual(message.sender_role, InquiryMessage.SenderRole.BUYER)
        self.assertEqual(self.inquiry.shop_unread, 2)
        self.notify.assert_called_once_with(
            self.shop, NEW_MESSAGE, inquiry=mock.ANY, message=message
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

    def test_no_notification_if_the_transaction_rolls_back(self):
        with self.captureOnCommitCallbacks(execute=True) as callbacks:
            try:
                with transaction.atomic():
                    send_message(self.buyer, self.inquiry.pk, "Hello", WEB)
                    raise RuntimeError
            except RuntimeError:
                pass

        self.assertEqual(callbacks, [])
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
