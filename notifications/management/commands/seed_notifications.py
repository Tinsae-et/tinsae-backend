"""Seed one read and one unread notification of every kind for a user (dev only)."""

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from notifications.kinds import KINDS
from notifications.models import Notification

# Realistic payloads, shaped like the real notify() call sites. The IDs are
# fake, so inquiry links lead to a 404 — this is for rendering the feed, not
# for following the CTA.
SAMPLE_PAYLOADS: dict[str, list[dict]] = {
    "inquiry_message": [
        {
            "message_id": 1,
            "inquiry_id": 1,
            "product_name": "iPhone 13 128GB",
            "body": "Is this still available? Can I come see it today?",
        },
        {"message_id": 2, "inquiry_id": 2, "product_name": "", "body": "Hello!"},
    ],
    "inquiry_reply": [
        {
            "message_id": 3,
            "inquiry_id": 3,
            "shop_name": "Bole Electronics",
            "body": "Yes, it is in stock. We close at 8pm.",
        },
        {"message_id": 4, "inquiry_id": 4, "shop_name": "", "body": "In stock."},
    ],
    "inquiry_unanswered": [
        {"inquiry_id": 5, "product_name": "Samsung A54"},
        {"inquiry_id": 6, "product_name": ""},
    ],
    "verification_reviewed": [
        {"request_id": 1, "state": "approved"},
        {"request_id": 2, "state": "rejected"},
    ],
}


class Command(BaseCommand):
    help = (
        "Create one unread and one read notification of every kind for a user, "
        "so the frontend feed can be tested without the inquiry flow. DEBUG only."
    )

    def add_arguments(self, parser):
        parser.add_argument("--user", required=True, help="Recipient's username.")

    def handle(self, *args, **options):
        # Fake rows in a real user's feed would be indistinguishable from real
        # ones, so this never runs outside development.
        if not settings.DEBUG:
            raise CommandError("seed_notifications only runs with DEBUG=True.")

        User = get_user_model()
        try:
            user = User.objects.get(username=options["user"])
        except User.DoesNotExist:
            raise CommandError(f"No user with username {options['user']!r}.") from None

        missing = set(KINDS) - set(SAMPLE_PAYLOADS)
        if missing:
            raise CommandError(f"No sample payload for kinds: {sorted(missing)}")

        # Rows are written directly, not through notify(): notify() hands them to
        # channels, which would send real Telegram messages and emails. The
        # timestamp in the dedupe key lets the command be re-run.
        now = timezone.now()
        stamp = now.strftime("%Y%m%d%H%M%S%f")
        rows = []
        for name, (unread_payload, read_payload) in SAMPLE_PAYLOADS.items():
            spec = KINDS[name]
            for i, (payload, read_at) in enumerate(
                [(unread_payload, None), (read_payload, now)]
            ):
                rows.append(
                    Notification(
                        recipient=user,
                        kind=name,
                        payload=payload,
                        link=spec.link(payload),
                        dedupe_key=f"seed:{name}:{stamp}:{i}",
                        read_at=read_at,
                    )
                )
        Notification.objects.bulk_create(rows)

        self.stdout.write(
            self.style.SUCCESS(
                f"Created {len(rows)} notifications for user {user.pk} "
                f"({len(SAMPLE_PAYLOADS)} kinds, one unread and one read each)."
            )
        )
