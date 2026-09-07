import re

from django.db import migrations

_BROKEN_SLUG_PATTERN = re.compile(r"^-\d+$")


def backfill_product_slugs(apps, schema_editor):
    Product = apps.get_model("catalog", "Product")
    for product in Product.objects.all():
        if not product.slug or _BROKEN_SLUG_PATTERN.match(product.slug):
            new_slug = f"product-{product.pk}"
            counter = 1
            while Product.objects.filter(slug=new_slug).exclude(pk=product.pk).exists():
                counter += 1
                new_slug = f"product-{product.pk}-{counter}"
            product.slug = new_slug
            product.save(update_fields=["slug"])


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("catalog", "0014_product_status_stock_metrics"),
    ]

    operations = [
        migrations.RunPython(backfill_product_slugs, reverse_code=noop_reverse),
    ]
