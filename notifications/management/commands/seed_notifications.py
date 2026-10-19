"""Create real notifications by playing a buyer-shop conversation (dev only)."""

from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from catalog.models import Product, ProductStatus
from inquiries.models import Inquiry, InquiryMessage
from inquiries.services import (
    UNANSWERED_AFTER,
    remind_unanswered_inquiries,
    send_message,
    start_inquiry,
)
from notifications.models import Notification

WEB = InquiryMessage.SentVia.WEB


class Command(BaseCommand):
    help = (
        "Play a real conversation between a buyer and a seller's shop through the "
        "inquiry services, so every notification comes from notify() exactly as "
        "in production: the seller gets inquiry_message and inquiry_unanswered, "
        "the buyer gets inquiry_reply. Telegram delivery is real too (if the shop "
        "is linked and the bot runs). DEBUG only."
    )

    def add_arguments(self, parser):
        parser.add_argument("--buyer", required=True, help="Buyer's username.")
        parser.add_argument("--seller", required=True, help="Shop owner's username.")

    def handle(self, *args, **options):
        # It writes real inquiries and messages and can message a real Telegram
        # chat, so it never runs outside development.
        if not settings.DEBUG:
            raise CommandError("seed_notifications only runs with DEBUG=True.")

        buyer = self._user(options["buyer"])
        seller = self._user(options["seller"])
        if buyer.pk == seller.pk:
            raise CommandError("--buyer and --seller must be different users.")
        shop = seller.shops.order_by("created_at").first()
        if shop is None:
            raise CommandError(f"User {options['seller']!r} owns no shop.")

        # Two different conversation keys are needed: one the shop answers, one
        # left waiting for the reminder. With fewer than two products the
        # product-less conversation fills in; with none, there is only one key.
        products = list(
            Product.objects.filter(shop=shop)
            .exclude(status=ProductStatus.DRAFT)
            .order_by("-created_at")[:2]
        )
        about = (products + [None])[:2]

        last_pk = Notification.objects.aggregate(last=Max("pk"))["last"] or 0

        try:
            with transaction.atomic():
                self._answered_conversation(buyer, seller, shop, about[0])
                if len(about) > 1:
                    self._waiting_conversation(buyer, shop, about[1])
                else:
                    self.stdout.write(
                        "Shop has no listings; skipped the unanswered reminder."
                    )
        except ValidationError as exc:
            raise CommandError(f"Inquiry service refused: {exc.messages}") from None

        created = (
            Notification.objects.filter(pk__gt=last_pk, recipient__in=[buyer, seller])
            .select_related("recipient")
            .order_by("pk")
        )
        for notification in created:
            self.stdout.write(
                f"  {notification.recipient.username}: {notification.kind} "
                f"-> {notification.link}"
            )
        self.stdout.write(self.style.SUCCESS(f"Created {len(created)} notifications."))

    def _user(self, username):
        User = get_user_model()
        try:
            return User.objects.get(username=username)
        except User.DoesNotExist:
            raise CommandError(f"No user with username {username!r}.") from None

    def _answered_conversation(self, buyer, seller, shop, product):
        inquiry = start_inquiry(
            buyer,
            shop,
            "Hi, is this still available? Can I see it today?",
            WEB,
            product,
        )
        send_message(
            seller, inquiry.pk, "Yes, it's in stock. We're open until 8pm.", WEB
        )
        self.stdout.write(f"Inquiry {inquiry.pk}: buyer asked, shop replied.")

    def _waiting_conversation(self, buyer, shop, product):
        inquiry = start_inquiry(
            buyer, shop, "What's your best price on this one?", WEB, product
        )
        # The reminder fires after the buyer has waited 24h. Backdating the one
        # timestamp the job reads is the only shortcut here; the reminder itself
        # comes from the real job. It is dedupe-keyed per inquiry, so a re-run
        # only adds a new buyer message, not a second reminder. The job also
        # reminds any other open inquiry in this database that is due.
        Inquiry.objects.filter(pk=inquiry.pk).update(
            last_message_at=timezone.now() - UNANSWERED_AFTER - timedelta(hours=1)
        )
        remind_unanswered_inquiries()
        self.stdout.write(f"Inquiry {inquiry.pk}: buyer asked, shop hasn't replied.")
