"""Idempotent setup for notification periodic task schedules in django-celery-beat."""

from django.conf import settings
from django.core.management.base import BaseCommand
from django_celery_beat.models import CrontabSchedule, PeriodicTask


class Command(BaseCommand):
    help = "Configure periodic schedules in django-celery-beat for the notification service."

    def handle(self, *args, **options):
        tz = getattr(settings, "TIME_ZONE", "Africa/Addis_Ababa")

        # Batched email digests: hourly at minute 0
        digest_crontab, _ = CrontabSchedule.objects.get_or_create(
            minute="0",
            hour="*",
            day_of_week="*",
            day_of_month="*",
            month_of_year="*",
            timezone=tz,
        )
        digest_task, digest_created = PeriodicTask.objects.update_or_create(
            name="notifications-send-email-digests",
            defaults={
                "task": "notifications.tasks.send_email_digests",
                "crontab": digest_crontab,
                "interval": None,
                "enabled": True,
            },
        )
        digest_action = "Created" if digest_created else "Updated"
        self.stdout.write(
            self.style.SUCCESS(
                f"{digest_action} PeriodicTask '{digest_task.name}' -> "
                f"{digest_task.task} (hourly at :00 {tz}, "
                f"enabled={digest_task.enabled})"
            )
        )

        # Remind shops of unanswered buyer inquiries (24h-7d): hourly at minute 30
        reminder_crontab, _ = CrontabSchedule.objects.get_or_create(
            minute="30",
            hour="*",
            day_of_week="*",
            day_of_month="*",
            month_of_year="*",
            timezone=tz,
        )
        reminder_task, reminder_created = PeriodicTask.objects.update_or_create(
            name="notifications-remind-unanswered-inquiries",
            defaults={
                "task": "inquiries.tasks.remind_unanswered_inquiries",
                "crontab": reminder_crontab,
                "interval": None,
                "enabled": True,
            },
        )
        reminder_action = "Created" if reminder_created else "Updated"
        self.stdout.write(
            self.style.SUCCESS(
                f"{reminder_action} PeriodicTask '{reminder_task.name}' -> "
                f"{reminder_task.task} (hourly at :30 {tz}, "
                f"enabled={reminder_task.enabled})"
            )
        )

        # Purge notifications older than 90 days: daily at 04:00 Africa/Addis_Ababa
        purge_crontab, _ = CrontabSchedule.objects.get_or_create(
            minute="0",
            hour="4",
            day_of_week="*",
            day_of_month="*",
            month_of_year="*",
            timezone=tz,
        )
        purge_task, purge_created = PeriodicTask.objects.update_or_create(
            name="notifications-purge-old-notifications",
            defaults={
                "task": "notifications.tasks.purge_old_notifications",
                "crontab": purge_crontab,
                "interval": None,
                "enabled": True,
            },
        )
        purge_action = "Created" if purge_created else "Updated"
        self.stdout.write(
            self.style.SUCCESS(
                f"{purge_action} PeriodicTask '{purge_task.name}' -> "
                f"{purge_task.task} (04:00 {tz}, enabled={purge_task.enabled})"
            )
        )
