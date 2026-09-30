from django.core.management.base import BaseCommand

from bot.services import sweep


class Command(BaseCommand):
    help = (
        "Reconciles Product.confirmation_status against the confirmation clock "
        "(CONFIRMATION_INTERVAL_DAYS) and expires past-deadline pending requests. "
        "Does NOT prompt (the weekly digest handles asking)."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--interval-days",
            "--staleness-days",
            dest="interval_days",
            type=int,
            default=None,
            help=(
                "Override settings.CONFIRMATION_INTERVAL_DAYS for this run. Changing "
                "the threshold rewrites confirmation_status for every product, so "
                "prefer the setting for anything recurring."
            ),
        )
        parser.add_argument(
            "--expiry-hours",
            type=int,
            default=None,
            help=(
                "Deprecated/ignored. Expiry is aligned with CONFIRMATION_HIDDEN_AFTER_DAYS."
            ),
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would change without writing or sending anything.",
        )

    def handle(self, *args, **options):
        interval_days = options.get("interval_days")
        dry_run = options.get("dry_run", False)

        result = sweep(interval_days=interval_days, dry_run=dry_run)

        summary = (
            f"Stale products: {result['stale_products']} (interval: {result['interval_days']}d) | "
            f"Newly stale: {result['newly_stale']} | "
            f"Restored to fresh: {result['restored_fresh']} | "
            f"Prompts sent: {result['prompts_sent']} | "
            f"Expired requests: {result['expired_requests']} | "
            f"Skipped: 0"
        )
        prefix = "[DRY RUN] " if dry_run else ""
        self.stdout.write(
            self.style.SUCCESS(f"{prefix}[Freshness Sweep Complete] {summary}")
        )
