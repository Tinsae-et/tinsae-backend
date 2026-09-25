from django.core.management.base import BaseCommand, CommandError

from catalog.taxonomy import ATTRIBUTES, PRODUCT_TYPES
from catalog.taxonomy.sync import TaxonomyError, sync_taxonomy


class Command(BaseCommand):
    help = (
        "Write the taxonomy in catalog/taxonomy/ (attributes, options, product "
        "types, category links) to the database. Safe to re-run; run on deploy "
        "after migrate."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report the changes without saving them.",
        )

    def handle(self, *args, dry_run=False, **options):
        try:
            changes = sync_taxonomy(ATTRIBUTES, PRODUCT_TYPES, dry_run=dry_run)
        except TaxonomyError as exc:
            raise CommandError(str(exc)) from exc

        for line in changes:
            self.stdout.write(f"  {line}")
        applied = [c for c in changes if not c.startswith("note:")]
        prefix = "Would apply" if dry_run else "Applied"
        self.stdout.write(
            self.style.SUCCESS(f"{prefix} {len(applied)} change(s).")
            if applied
            else "Taxonomy already up to date."
        )
