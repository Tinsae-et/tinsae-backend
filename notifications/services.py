"""Business logic for the notifications service."""

import logging
from collections import defaultdict
from datetime import timedelta

from django.conf import settings
from django.core.mail import send_mail
from django.db import IntegrityError, transaction
from django.utils import timezone

from .channels import CHANNELS
from .kinds import KINDS, get_kind
from .models import Notification

logger = logging.getLogger(__name__)


def notify(user, kind: str, payload: dict) -> Notification | None:
    """Record that `user` should hear about `kind`, and hand it to its channels.

    Runs inside the caller's transaction: the notification row and any Telegram
    outbox row commit or roll back with the domain event that caused them.
    Returns None if this event was already notified for this user (dedupe).
    """
    spec = get_kind(kind)
    try:
        with transaction.atomic():  # savepoint, like bot.outbox.enqueue
            notification = Notification.objects.create(
                recipient=user,
                kind=kind,
                payload=payload,
                link=spec.link(payload),
                dedupe_key=spec.dedupe_key(payload),
            )
    except IntegrityError:
        return None

    for channel in spec.channels:
        CHANNELS[channel].deliver(notification, spec)

    return notification


def mark_read(user, notification_id: int) -> Notification:
    """Mark a notification as read, scoped to the recipient user.

    Raises Notification.DoesNotExist if notification_id does not exist,
    belongs to another user (preventing IDOR probing), or is of an unknown kind.
    """
    notification = Notification.objects.get(
        pk=notification_id,
        recipient=user,
        kind__in=KINDS.keys(),
    )
    if notification.read_at is None:
        now = timezone.now()
        notification.read_at = now
        notification.updated_at = now
        notification.save(update_fields=["read_at", "updated_at"])
    return notification


def unread_count(user) -> int:
    """Return the number of unread notifications for `user` among known kinds."""
    return Notification.objects.filter(
        recipient=user,
        read_at__isnull=True,
        kind__in=KINDS.keys(),
    ).count()


def mark_all_read(user) -> int:
    """Mark all unread notifications of known kinds for `user` as read.

    Runs a single bulk UPDATE without loading rows into memory.
    """
    now = timezone.now()
    return Notification.objects.filter(
        recipient=user,
        read_at__isnull=True,
        kind__in=KINDS.keys(),
    ).update(read_at=now, updated_at=now)


def send_email_digests() -> int:
    """Send batched email digests to users with pending unread notifications.

    Candidates:
    - email_sent_at IS NULL and read_at IS NULL
    - kind has email_batched=True
    - created_at <= now - NOTIFICATION_EMAIL_DELAY_MINUTES (allow reading in-app first)
    - created_at >= now - 7 days (bounded scan window)

    Groups candidates by recipient. For each recipient, locks their candidate
    rows in a short transaction with select_for_update(skip_locked=True), sends
    a single plain-text digest email, and marks those rows with email_sent_at.
    """
    now = timezone.now()
    delay_minutes = getattr(settings, "NOTIFICATION_EMAIL_DELAY_MINUTES", 15)
    delay_cutoff = now - timedelta(minutes=delay_minutes)
    window_cutoff = now - timedelta(days=7)
    batched_kinds = [k.name for k in KINDS.values() if k.email_batched]

    # Fetch candidates in one query with select_related to avoid N+1 queries
    candidates = list(
        Notification.objects.select_related("recipient")
        .filter(
            recipient__is_active=True,
            email_sent_at__isnull=True,
            read_at__isnull=True,
            kind__in=batched_kinds,
            created_at__lte=delay_cutoff,
            created_at__gte=window_cutoff,
        )
        .order_by("recipient_id", "created_at")
    )
    if not candidates:
        return 0

    grouped_candidates: dict = defaultdict(list)
    for notif in candidates:
        grouped_candidates[notif.recipient].append(notif)

    emails_sent = 0
    for recipient, notif_list in grouped_candidates.items():
        if not recipient.email:
            # Recipient without an email -> skip (leave rows; age out of the 7-day window)
            logger.info(
                "Skipping email digest for user %s: no email address registered",
                recipient.pk,
            )
            continue

        candidate_pks = [n.pk for n in notif_list]
        try:
            with transaction.atomic():
                locked_notifs = list(
                    Notification.objects.select_for_update(
                        of=("self",), skip_locked=True
                    )
                    .filter(
                        pk__in=candidate_pks,
                        email_sent_at__isnull=True,
                        read_at__isnull=True,
                    )
                    .order_by("created_at")
                )
                if not locked_notifs:
                    continue

                count = len(locked_notifs)
                subject = (
                    f"You have {count} unread notification{'s' if count > 1 else ''} on"
                    " Tinsae"
                )
                base_url = settings.FRONTEND_URL.rstrip("/")
                lines = [
                    f"Hello {recipient.username},",
                    "",
                    f"You have {count} unread notification{'s' if count > 1 else ''} on"
                    " Tinsae:",
                ]
                for notif in locked_notifs:
                    url = f"{base_url}{notif.link}" if notif.link else base_url
                    lines.append(f"- {url}")
                lines.extend(["", "Log in to view and reply.", "", "-- Tinsae"])
                body = "\n".join(lines)

                send_mail(
                    subject=subject,
                    message=body,
                    from_email=settings.DEFAULT_FROM_EMAIL,
                    recipient_list=[recipient.email],
                    fail_silently=False,
                )

                Notification.objects.filter(
                    pk__in=[n.pk for n in locked_notifs]
                ).update(
                    email_sent_at=now,
                    updated_at=now,
                )
                emails_sent += 1
        except Exception:
            # Atomic block rollback leaves email_sent_at NULL so this recipient
            # can be retried on the next digest run without starving others now.
            logger.exception("Email digest failed for user %s", recipient.pk)
            continue

    return emails_sent


def purge_old_notifications(batch_size: int = 5000) -> int:
    """Delete notifications older than NOTIFICATION_RETENTION_DAYS in batches."""
    retention_days = getattr(settings, "NOTIFICATION_RETENTION_DAYS", 90)
    cutoff = timezone.now() - timedelta(days=retention_days)
    total_deleted = 0
    while True:
        pks = list(
            Notification.objects.filter(created_at__lt=cutoff).values_list(
                "pk", flat=True
            )[:batch_size]
        )
        if not pks:
            break
        deleted, _ = Notification.objects.filter(pk__in=pks).delete()
        total_deleted += deleted
        if len(pks) < batch_size:
            break
    return total_deleted
