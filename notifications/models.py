from django.conf import settings
from django.db import models
from django.db.models import Index, Q, UniqueConstraint

from core.models import BaseModel


class Notification(BaseModel):
    """An event a user should know about.

    Written inside the caller's transaction alongside the domain event that caused it.
    The in-app inbox reads this table directly; channels (Telegram, email)
    deliver to external destinations asynchronously or via the outbox pattern.
    """

    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="notifications",
    )
    kind = models.CharField(max_length=50)
    payload = models.JSONField(
        default=dict,
        help_text="IDs and short strings only — never model instances.",
    )
    link = models.CharField(
        max_length=255,
        blank=True,
        help_text="Frontend path, e.g. /inquiries/12",
    )
    dedupe_key = models.CharField(max_length=255)
    read_at = models.DateTimeField(null=True, blank=True)
    email_sent_at = models.DateTimeField(null=True, blank=True)

    class Meta(BaseModel.Meta):
        ordering = ["-created_at"]
        constraints = [
            UniqueConstraint(
                fields=["recipient", "dedupe_key"],
                name="unique_notification_per_recipient_event",
            ),
        ]
        indexes = [
            Index(
                fields=["recipient", "read_at", "-created_at"],
                name="notif_inbox_idx",
            ),
            Index(fields=["created_at"], name="notif_created_idx"),
            Index(
                fields=["kind", "created_at"],
                name="notif_email_pending_idx",
                condition=Q(email_sent_at__isnull=True, read_at__isnull=True),
            ),
        ]

    def __str__(self) -> str:
        return (
            f"Notification({self.pk}, recipient={self.recipient_id}, kind={self.kind})"
        )
