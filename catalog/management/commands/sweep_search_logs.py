from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from catalog.models import SearchLog


class Command(BaseCommand):
    help = (
        "Deletes SearchLog rows older than settings.SEARCH_LOG_RETENTION_DAYS. "
        "Run on the same schedule as sweep_freshness."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--retention-days",
            type=int,
            default=None,
            help=(
                "Override settings.SEARCH_LOG_RETENTION_DAYS for this run. Unlike "
                "the freshness sweep this writes nothing back to the catalogue, "
                "so a one-off override is safe -- but it deletes, so check the "
                "count with --dry-run first."
            ),
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report how many rows would be deleted without deleting them.",
        )

    def handle(self, *args, **options):
        retention_days = options["retention_days"]
        if retention_days is None:
            retention_days = settings.SEARCH_LOG_RETENTION_DAYS

        cutoff = timezone.now() - timedelta(days=retention_days)
        expired = SearchLog.objects.filter(created_at__lt=cutoff)
        count = expired.count()

        if not options["dry_run"] and count:
            # Bulk delete rather than iterating: there are no signals or file
            # cleanup on this model, and the table is the highest-volume one in
            # the app -- a per-row delete would be the slowest thing in the cron.
            expired.delete()

        prefix = "[DRY RUN] " if options["dry_run"] else ""
        self.stdout.write(
            self.style.SUCCESS(
                f"{prefix}[Search Log Sweep Complete] "
                f"Retention: {retention_days}d | Deleted: {count}"
            )
        )
