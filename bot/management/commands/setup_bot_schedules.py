"""Idempotent setup for bot periodic task schedules in django-celery-beat."""

from django.conf import settings
from django.core.management.base import BaseCommand
from django_celery_beat.models import CrontabSchedule, IntervalSchedule, PeriodicTask


class Command(BaseCommand):
    help = "Configure periodic schedules in django-celery-beat for the Telegram bot."

    def handle(self, *args, **options):
        # Drain outbox: every 15s
        schedule, _ = IntervalSchedule.objects.get_or_create(
            every=15,
            period=IntervalSchedule.SECONDS,
        )
        task, created = PeriodicTask.objects.update_or_create(
            name="bot-drain-outbox",
            defaults={
                "task": "bot.tasks.drain_outbox",
                "interval": schedule,
                "crontab": None,
                "enabled": True,
            },
        )
        action = "Created" if created else "Updated"
        self.stdout.write(
            self.style.SUCCESS(
                f"{action} PeriodicTask '{task.name}' -> {task.task} (every 15s, enabled={task.enabled})"
            )
        )

        # Weekly confirmation digest: a morning window, not a single 08:00 tick.
        # The job is idempotent per shop per day (unique (shop, for_date) + the
        # cadence gate), so firing at :00 across 08:00-11:00 means a beat restart
        # or DNS blip around 08:00 does not lose the whole day's digest — it
        # catches up at the next hour.
        tz = getattr(settings, "TIME_ZONE", "Africa/Addis_Ababa")
        crontab, _ = CrontabSchedule.objects.get_or_create(
            minute="0",
            hour="8,9,10,11",
            day_of_week="*",
            day_of_month="*",
            month_of_year="*",
            timezone=tz,
        )
        digest_task, digest_created = PeriodicTask.objects.update_or_create(
            name="bot-send-due-digests",
            defaults={
                "task": "bot.tasks.send_due_digests",
                "crontab": crontab,
                "interval": None,
                "enabled": True,
            },
        )
        digest_action = "Created" if digest_created else "Updated"
        self.stdout.write(
            self.style.SUCCESS(
                f"{digest_action} PeriodicTask '{digest_task.name}' -> {digest_task.task} (08:00-11:00 hourly {tz}, enabled={digest_task.enabled})"
            )
        )

        # Hourly freshness reconciliation and expiry sweep: every hour at minute 0
        sweep_crontab, _ = CrontabSchedule.objects.get_or_create(
            minute="0",
            hour="*",
            day_of_week="*",
            day_of_month="*",
            month_of_year="*",
            timezone=tz,
        )
        sweep_task, sweep_created = PeriodicTask.objects.update_or_create(
            name="bot-sweep",
            defaults={
                "task": "bot.tasks.sweep",
                "crontab": sweep_crontab,
                "interval": None,
                "enabled": True,
            },
        )
        sweep_action = "Created" if sweep_created else "Updated"
        self.stdout.write(
            self.style.SUCCESS(
                f"{sweep_action} PeriodicTask '{sweep_task.name}' -> {sweep_task.task} (hourly at :00 {tz}, enabled={sweep_task.enabled})"
            )
        )

        # Reminder for unanswered open digests: an afternoon window for the same
        # reason. `reminded_at` makes a repeat run a no-op, so it fires at most
        # once per digest even across the window.
        reminder_crontab, _ = CrontabSchedule.objects.get_or_create(
            minute="0",
            hour="13,14,15,16",
            day_of_week="*",
            day_of_month="*",
            month_of_year="*",
            timezone=tz,
        )
        reminder_task, reminder_created = PeriodicTask.objects.update_or_create(
            name="bot-send-reminders",
            defaults={
                "task": "bot.tasks.send_reminders",
                "crontab": reminder_crontab,
                "interval": None,
                "enabled": True,
            },
        )
        reminder_action = "Created" if reminder_created else "Updated"
        self.stdout.write(
            self.style.SUCCESS(
                f"{reminder_action} PeriodicTask '{reminder_task.name}' -> {reminder_task.task} (13:00-16:00 hourly {tz}, enabled={reminder_task.enabled})"
            )
        )

        # Prune old dedupe updates and terminal outbox messages: daily at 03:00 Africa/Addis_Ababa
        prune_crontab, _ = CrontabSchedule.objects.get_or_create(
            minute="0",
            hour="3",
            day_of_week="*",
            day_of_month="*",
            month_of_year="*",
            timezone=tz,
        )
        prune_task, prune_created = PeriodicTask.objects.update_or_create(
            name="bot-prune",
            defaults={
                "task": "bot.tasks.prune",
                "crontab": prune_crontab,
                "interval": None,
                "enabled": True,
            },
        )
        prune_action = "Created" if prune_created else "Updated"
        self.stdout.write(
            self.style.SUCCESS(
                f"{prune_action} PeriodicTask '{prune_task.name}' -> {prune_task.task} (03:00 {tz}, enabled={prune_task.enabled})"
            )
        )
