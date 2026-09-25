from collections import Counter

from django.core.management.base import BaseCommand
from django.db import transaction

from catalog.models import Category, Product
from catalog.specs import category_schema, validate_specs
from catalog.specs_migration import migrate_product_specs

BATCH = 500


class Command(BaseCommand):
    help = (
        "Convert existing products' specs to the category schemas "
        "(run sync_taxonomy first). Dry run by default; --apply saves."
    )

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", help="Save the changes.")
        parser.add_argument("--category", help="Only this category slug.")

    def handle(self, *args, apply=False, category=None, **options):
        categories = Category.objects.filter(product_type__isnull=False)
        if category:
            categories = categories.filter(slug=category)
        if not categories.exists():
            self.stdout.write("No categories with a product type. Run sync_taxonomy.")
            return

        for cat in categories.select_related("product_type"):
            schema = category_schema(cat)
            defined = {f["key"] for f in schema["fields"]}
            set_counts, missing = Counter(), Counter()
            changed_total = total = 0
            batch = []

            with transaction.atomic():
                products = Product.objects.filter(category=cat).only(
                    "id", "specs", "condition"
                )
                for product in products.iterator(chunk_size=BATCH):
                    total += 1
                    before = product.specs or {}
                    after = migrate_product_specs(
                        cat.slug, schema, before, product.condition
                    )
                    for key in after.keys() - before.keys():
                        set_counts[key] += 1
                    for key in before.keys() & after.keys():
                        if before[key] != after[key]:
                            set_counts[key] += 1
                    # Strict on schema keys, silent about the legacy extras
                    # (`display`, `chip`...) that are kept on purpose.
                    errors = validate_specs(schema, after, condition=product.condition)
                    for key in errors.keys() & defined:
                        missing[key] += 1
                    if after != before:
                        changed_total += 1
                        product.specs = after
                        batch.append(product)
                    if apply and len(batch) >= BATCH:
                        Product.objects.bulk_update(batch, ["specs"])
                        batch = []
                if apply and batch:
                    Product.objects.bulk_update(batch, ["specs"])

            self.stdout.write(self.style.MIGRATE_HEADING(f"{cat.slug}"))
            self.stdout.write(f"  products: {total}, changed: {changed_total}")
            for key, count in set_counts.most_common():
                self.stdout.write(f"  set {key}: {count}")
            for key, count in missing.most_common():
                self.stdout.write(f"  still missing/invalid {key}: {count}")

        if not apply:
            self.stdout.write(
                self.style.WARNING("Dry run: nothing saved. Add --apply.")
            )
