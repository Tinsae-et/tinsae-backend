from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from bot.models import ConfirmationRequest
from bot.services import can_prompt, send_confirmation_prompt
from catalog.models import ConfirmationStatus, Product


class Command(BaseCommand):
    help = (
        "Reconciles Product.confirmation_status against the weekly confirmation clock "
        "(CONFIRMATION_INTERVAL_DAYS), expires stale pending requests, and prompts "
        "shop owners over Telegram."
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
            help="Override settings.CONFIRMATION_REQUEST_EXPIRY_HOURS for this run.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would change without writing or sending anything.",
        )

    def handle(self, *args, **options):
        interval_days = options["interval_days"]
        if interval_days is None:
            interval_days = settings.CONFIRMATION_INTERVAL_DAYS
        expiry_hours = options["expiry_hours"]
        if expiry_hours is None:
            expiry_hours = settings.CONFIRMATION_REQUEST_EXPIRY_HOURS

        dry_run = options["dry_run"]
        now = timezone.now()
        staleness_cutoff = now - timedelta(days=interval_days)
        expiry_cutoff = now - timedelta(hours=expiry_hours)

        # 1. Expire requests the merchant never answered. This has to run first:
        # an unanswered request blocks re-prompting until it is cleared.
        #
        # Their products drop to UNCONFIRMED rather than staying STALE. Both mean
        # "past the clock", but only this one carries evidence: we reached the
        # merchant, they said nothing, and the silence is the finding. A product
        # nobody has got round to asking about yet is not the same thing, and
        # collapsing the two loses the signal that separates a quiet catalogue
        # from a disengaged one.
        expired_qs = ConfirmationRequest.objects.filter(
            status="pending", created_at__lt=expiry_cutoff
        )
        unanswered_pks = list(expired_qs.values_list("product_id", flat=True))
        expired_count = len(unanswered_pks)
        if not dry_run:
            expired_qs.update(status="expired")
            if unanswered_pks:
                Product.objects.filter(pk__in=unanswered_pks).update(
                    confirmation_status=ConfirmationStatus.UNCONFIRMED
                )

        # 2. Reconcile confirmation_status in both directions. The predicate lives
        # on ProductQuerySet so this command and the bot's /myproducts listing
        # cannot disagree about who needs asking.
        stale_products = list(
            Product.objects.needing_confirmation(staleness_cutoff).select_related(
                "shop"
            )
        )
        # UNCONFIRMED is excluded, not merely already-STALE: step 1 has just set
        # it on this run, and both values satisfy `needing_confirmation`. Without
        # the exclusion this line walks straight back over that write and every
        # unanswered prompt is downgraded to STALE by the same sweep that
        # recorded it. Only a merchant's answer moves a product back up, and it
        # does that through `apply_confirmation`.
        held_back = (ConfirmationStatus.STALE, ConfirmationStatus.UNCONFIRMED)
        newly_stale_pks = [
            p.pk for p in stale_products if p.confirmation_status not in held_back
        ]
        went_fresh_pks = list(
            Product.objects.confirmed_since(staleness_cutoff)
            .exclude(confirmation_status=ConfirmationStatus.FRESH)
            .values_list("pk", flat=True)
        )

        if not dry_run:
            if newly_stale_pks:
                Product.objects.filter(pk__in=newly_stale_pks).update(
                    confirmation_status=ConfirmationStatus.STALE
                )
            if went_fresh_pks:
                Product.objects.filter(pk__in=went_fresh_pks).update(
                    confirmation_status=ConfirmationStatus.FRESH
                )

        # 3. Prompt every product that is currently stale, not just the ones that
        # changed on this run -- an unanswered prompt from last week should be
        # retried once its request has expired. can_prompt owns the gates for
        # both paths, so a dry run cannot report a prompt the real run would skip.
        prompted_count = 0
        skipped_count = 0
        for product in stale_products:
            if dry_run:
                sent = can_prompt(product, expiry_cutoff=expiry_cutoff)
            else:
                sent = send_confirmation_prompt(product) is not None
            prompted_count += 1 if sent else 0
            skipped_count += 0 if sent else 1

        summary = (
            f"Stale products: {len(stale_products)} (interval: {interval_days}d) | "
            f"Newly stale: {len(newly_stale_pks)} | "
            f"Restored to fresh: {len(went_fresh_pks)} | "
            f"Prompts sent: {prompted_count} | "
            f"Expired requests: {expired_count} | "
            f"Skipped: {skipped_count}"
        )
        prefix = "[DRY RUN] " if dry_run else ""
        self.stdout.write(
            self.style.SUCCESS(f"{prefix}[Freshness Sweep Complete] {summary}")
        )
