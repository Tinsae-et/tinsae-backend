from django.core.management.base import BaseCommand

from catalog.models import Category

STARTER_CATEGORIES = [
    {"slug": "phones", "name": "Phones", "display_order": 0},
    {"slug": "accessories", "name": "Accessories", "display_order": 1},
]


class Command(BaseCommand):
    help = "Idempotently load the v1 starter categories."

    def handle(self, *args, **options):
        for data in STARTER_CATEGORIES:
            category, created = Category.objects.get_or_create(
                slug=data["slug"],
                defaults={"name": data["name"], "display_order": data["display_order"]},
            )
            self.stdout.write(f"{'Created' if created else 'Exists'}: {category.name}")
