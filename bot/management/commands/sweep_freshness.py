from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db.models.functions import Coalesce
from django.utils import timezone

from bot.models import ConfirmationRequest
from bot.services import send_confirmation_prompt
from catalog.models import ConfirmationStatus, Product


class Command(BaseCommand):
    help = (
        "Reconciles Product.confirmation_status against the confirmation clock, "
        "expires stale pending requests, and prompts shop owners over Telegram."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--staleness-days",
            type=int,
            default=None,
            help=(
                "Override settings.PRODUCT_STALENESS_DAYS for this run. Changing "
                "the threshold rewrites confirmation_status for every product, so "
                "prefer the setting for anything recurring."
            ),
        )
        parser.add_argument(
            "--expiry-hours",
            type=int,
            default=None,
            help="Override settings.CONFIRMATION_REQUEST_EXPIRY_HOURS for this run.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would change without writing or sending anything.",
        )

    def handle(self, *args, **options):
        staleness_days = options["staleness_days"]
        if staleness_days is None:
            staleness_days = settings.PRODUCT_STALENESS_DAYS
        expiry_hours = options["expiry_hours"]
        if expiry_hours is None:
            expiry_hours = settings.CONFIRMATION_REQUEST_EXPIRY_HOURS

        dry_run = options["dry_run"]
        now = timezone.now()
        staleness_cutoff = now - timedelta(days=staleness_days)
        expiry_cutoff = now - timedelta(hours=expiry_hours)

        # 1. Expire requests the merchant never answered. This has to run first:
        # an unanswered request blocks re-prompting until it is cleared.
        expired_qs = ConfirmationRequest.objects.filter(
            status="pending", created_at__lt=expiry_cutoff
        )
        expired_count = expired_qs.count()
        if not dry_run:
            expired_qs.update(status="expired")

        # 2. Reconcile confirmation_status in both directions. A product that has
        # never been confirmed is measured from when it was listed, so a new
        # listing is not stale on day one. Unavailable products are included --
        # "out of stock" is a claim that also goes out of date, and excluding
        # them would strand every denied product as permanently unreviewed.
        scored = Product.objects.annotate(
            last_signal=Coalesce("last_confirmed_at", "created_at")
        )
        went_stale = list(
            scored.filter(last_signal__lt=staleness_cutoff)
            .exclude(confirmation_status=ConfirmationStatus.STALE)
            .select_related("shop")
        )
        went_fresh_pks = list(
            scored.filter(last_signal__gte=staleness_cutoff)
            .exclude(confirmation_status=ConfirmationStatus.FRESH)
            .values_list("pk", flat=True)
        )

        if not dry_run:
            if went_stale:
                Product.objects.filter(pk__in=[p.pk for p in went_stale]).update(
                    confirmation_status=ConfirmationStatus.STALE
                )
            if went_fresh_pks:
                Product.objects.filter(pk__in=went_fresh_pks).update(
                    confirmation_status=ConfirmationStatus.FRESH
                )

        # 3. Prompt every product that is currently stale, not just the ones that
        # changed on this run -- an unanswered prompt from last week should be
        # retried once its request has expired. send_confirmation_prompt owns the
        # reachability and duplicate checks; None means skipped or send failed.
        stale_products = list(
            scored.filter(last_signal__lt=staleness_cutoff).select_related("shop")
        )
        prompted_count = 0
        skipped_count = 0
        for product in stale_products:
            if dry_run:
                eligible = bool(product.shop.telegram_chat_id) and not (
                    ConfirmationRequest.objects.filter(
                        product=product, status="pending"
                    ).exists()
                )
                prompted_count += 1 if eligible else 0
                skipped_count += 0 if eligible else 1
            elif send_confirmation_prompt(product) is not None:
                prompted_count += 1
            else:
                skipped_count += 1

        summary = (
            f"Stale products: {len(stale_products)} | "
            f"Newly stale: {len(went_stale)} | "
            f"Restored to fresh: {len(went_fresh_pks)} | "
            f"Prompts sent: {prompted_count} | "
            f"Expired requests: {expired_count} | "
            f"Skipped: {skipped_count}"
        )
        prefix = "[DRY RUN] " if dry_run else ""
        self.stdout.write(
            self.style.SUCCESS(f"{prefix}[Freshness Sweep Complete] {summary}")
        )
