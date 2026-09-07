import re

from django.db import migrations

_BROKEN_SLUG_PATTERN = re.compile(r"^-\d+$")


def backfill_shop_slugs(apps, schema_editor):
    Shop = apps.get_model("shops", "Shop")
    for shop in Shop.objects.all():
        if not shop.slug or _BROKEN_SLUG_PATTERN.match(shop.slug):
            new_slug = f"shop-{shop.pk}"
            counter = 1
            while Shop.objects.filter(slug=new_slug).exclude(pk=shop.pk).exists():
                counter += 1
                new_slug = f"shop-{shop.pk}-{counter}"
            shop.slug = new_slug
            shop.save(update_fields=["slug"])


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("shops", "0003_shop_latitude_shop_longitude"),
    ]

    operations = [
        migrations.RunPython(backfill_shop_slugs, reverse_code=noop_reverse),
    ]
