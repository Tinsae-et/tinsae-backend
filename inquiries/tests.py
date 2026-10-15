from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from bot.models import OutboundMessage
from inquiries.models import Inquiry, InquiryMessage
from inquiries.services import close_inquiry, mark_read, send_message, start_inquiry
from shops.models import Shop


class InquiryServiceTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.buyer = user_model.objects.create_user(
            username="buyer", password="test-password", telegram_chat_id="buyer-chat"
        )
        self.owner = user_model.objects.create_user(
            username="owner", password="test-password"
        )
        self.shop = Shop.objects.create(
            name="Test Shop", owner=self.owner, telegram_chat_id="shop-chat"
        )

    def test_start_inquiry_creates_and_sends_first_message(self):
        inquiry = start_inquiry(self.buyer, self.shop, body="Is this available?")

        self.assertEqual(Inquiry.objects.count(), 1)
        message = InquiryMessage.objects.get(inquiry=inquiry)
        self.assertEqual(message.sender_role, InquiryMessage.SenderRole.BUYER)
        self.assertEqual(message.sent_via, InquiryMessage.SentVia.TELEGRAM)
        inquiry.refresh_from_db()
        self.assertEqual(inquiry.shop_unread, 1)
        self.assertEqual(inquiry.buyer_unread, 0)

    def test_start_inquiry_reuses_open_conversation(self):
        inquiry = start_inquiry(self.buyer, self.shop, body="First message")

        continued = start_inquiry(self.buyer, self.shop, body="Second message")

        self.assertEqual(continued.pk, inquiry.pk)
        self.assertEqual(Inquiry.objects.count(), 1)
        self.assertEqual(continued.messages.count(), 2)

    def test_start_inquiry_prefers_an_open_conversation_over_a_closed_one(self):
        open_inquiry = Inquiry.objects.create(
            buyer=self.buyer, shop=self.shop, status=Inquiry.Status.OPEN
        )
        Inquiry.objects.create(
            buyer=self.buyer, shop=self.shop, status=Inquiry.Status.CLOSED
        )

        continued = start_inquiry(self.buyer, self.shop, body="Continue here")

        self.assertEqual(continued.pk, open_inquiry.pk)
        self.assertEqual(Inquiry.objects.filter(status=Inquiry.Status.OPEN).count(), 1)

    def test_start_inquiry_reopens_the_existing_closed_conversation(self):
        inquiry = Inquiry.objects.create(
            buyer=self.buyer, shop=self.shop, status=Inquiry.Status.CLOSED
        )

        continued = start_inquiry(self.buyer, self.shop, body="Writing again")

        self.assertEqual(continued.pk, inquiry.pk)
        self.assertEqual(Inquiry.objects.count(), 1)
        continued.refresh_from_db()
        self.assertEqual(continued.status, Inquiry.Status.OPEN)

    def test_send_message_reopens_inquiry_and_increments_other_side_unread(self):
        inquiry = Inquiry.objects.create(
            buyer=self.buyer,
            shop=self.shop,
            status=Inquiry.Status.CLOSED,
            buyer_unread=2,
            shop_unread=3,
        )
        before = timezone.now()

        message = send_message(
            inquiry,
            InquiryMessage.SenderRole.SHOP,
            "We have it in stock.",
            InquiryMessage.SentVia.WEB,
        )

        inquiry.refresh_from_db()
        self.assertEqual(message.sent_via, InquiryMessage.SentVia.WEB)
        self.assertEqual(inquiry.status, Inquiry.Status.OPEN)
        self.assertEqual(inquiry.buyer_unread, 3)
        self.assertEqual(inquiry.shop_unread, 3)
        self.assertGreaterEqual(inquiry.last_message_at, before)

    def test_invalid_body_does_not_change_inquiry_or_create_message(self):
        inquiry = Inquiry.objects.create(buyer=self.buyer, shop=self.shop)

        with self.assertRaises(ValidationError):
            send_message(
                inquiry,
                InquiryMessage.SenderRole.BUYER,
                "  ",
                InquiryMessage.SentVia.WEB,
            )

        inquiry.refresh_from_db()
        self.assertEqual(inquiry.shop_unread, 0)
        self.assertEqual(inquiry.messages.count(), 0)
        self.assertEqual(OutboundMessage.objects.count(), 0)

    def test_mark_read_clears_only_the_selected_participants_counter(self):
        inquiry = Inquiry.objects.create(
            buyer=self.buyer,
            shop=self.shop,
            buyer_unread=2,
            shop_unread=3,
        )

        result = mark_read(inquiry, InquiryMessage.SenderRole.BUYER)

        result.refresh_from_db()
        self.assertEqual(result.buyer_unread, 0)
        self.assertEqual(result.shop_unread, 3)

    def test_mark_read_rejects_unknown_role(self):
        inquiry = Inquiry.objects.create(buyer=self.buyer, shop=self.shop)

        with self.assertRaises(ValidationError):
            mark_read(inquiry, "admin")

    def test_close_inquiry_closes_and_returns_the_locked_inquiry(self):
        inquiry = Inquiry.objects.create(buyer=self.buyer, shop=self.shop)

        result = close_inquiry(inquiry)

        result.refresh_from_db()
        self.assertEqual(result.status, Inquiry.Status.CLOSED)
