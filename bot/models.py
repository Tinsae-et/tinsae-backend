import secrets
from datetime import timedelta

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone

from core.models import BaseModel

# Ambiguous glyphs are omitted: these codes get read aloud down a phone line and
# retyped on a handset, where O/0 and I/1/L are the same character to everyone
# except the parser.
LINK_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
LINK_CODE_PREFIX = "TIN-"
LINK_CODE_LENGTH = 7


def generate_link_code() -> str:
    body = "".join(secrets.choice(LINK_CODE_ALPHABET) for _ in range(LINK_CODE_LENGTH))
    return f"{LINK_CODE_PREFIX}{body}"


def default_link_code_expiry():
    return timezone.now() + timedelta(hours=settings.TELEGRAM_LINK_CODE_TTL_HOURS)


class ShopLinkCode(BaseModel):
    """A one-time credential that binds a Telegram chat to a Shop.

    Issued in the admin, handed to the merchant, and spent the first time it
    reaches the bot. The code is the *only* thing that names the shop — nothing
    is derived from the message or from the chat — which is why it is single-use,
    short-lived, and spent under a row lock.

    Status is derived, not stored: `used_at` and `expires_at` are the facts and
    `is_used`/`is_expired` read them. A stored status column would be a third
    thing to keep in agreement with the other two, the way
    `Product.confirmation_status` has to be swept.
    """

    shop = models.ForeignKey(
        "shops.Shop",
        on_delete=models.CASCADE,
        related_name="link_codes",
    )
    code = models.CharField(max_length=16, unique=True, blank=True)
    expires_at = models.DateTimeField(default=default_link_code_expiry)
    used_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When a chat spent this code. NULL means it is still live.",
    )
    used_by_chat_id = models.CharField(
        max_length=50,
        blank=True,
        help_text="Which chat spent it — kept for audit after a re-link.",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="issued_link_codes",
    )

    class Meta(BaseModel.Meta):
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"ShopLinkCode({self.code} -> {self.shop.name})"

    @property
    def is_used(self) -> bool:
        return self.used_at is not None

    @property
    def is_expired(self) -> bool:
        return self.expires_at <= timezone.now()

    @property
    def is_live(self) -> bool:
        return not self.is_used and not self.is_expired

    def save(self, *args, **kwargs):
        # Assigned blind: `unique=True` is the real guard and `issue_link_code`
        # retries on IntegrityError. Deliberately not the read-then-write
        # `while ... .exists()` loop the slug fields use — that one races.
        if not self.code:
            self.code = generate_link_code()
        super().save(*args, **kwargs)


class ConfirmationRequest(BaseModel):
    STATUS_CHOICES = [
        ("pending", "Pending"),
        ("confirmed", "Confirmed"),
        ("sold_out", "Sold out"),
        ("expired", "Expired"),
    ]

    product = models.ForeignKey(
        "catalog.Product",
        on_delete=models.CASCADE,
        related_name="confirmation_requests",
    )
    shop = models.ForeignKey(
        "shops.Shop",
        on_delete=models.CASCADE,
        related_name="confirmation_requests",
    )
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default="pending",
    )
    responded_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text=(
            "When the merchant answered. Stays NULL for expired requests — an "
            "expiry is the absence of an answer, not an answer."
        ),
    )
    channel = models.CharField(max_length=20, default="telegram")
    telegram_message_id = models.CharField(
        max_length=100,
        blank=True,
        null=True,
    )

    class Meta(BaseModel.Meta):
        ordering = ["-created_at"]
        constraints = [
            # The "is one already pending?" check in services.py is a read
            # followed by a write. Two sweeps racing would both pass the read and
            # send the merchant two prompts for the same product. Let the database
            # settle it.
            models.UniqueConstraint(
                fields=["product"],
                condition=Q(status="pending"),
                name="unique_pending_confirmation_per_product",
            )
        ]

    # There is no separate `sent_at`: the prompt goes out in the same transaction
    # that creates the row, so `created_at` is the send time, and `sweep_freshness`
    # keys the expiry window off it. One clock, not two.

    def __str__(self) -> str:
        return (
            f"ConfirmationRequest({self.product.name} - "
            f"{self.shop.name} [{self.status}])"
        )
