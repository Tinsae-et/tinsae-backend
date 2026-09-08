from django.core.management.base import BaseCommand

from catalog.models import Category

STARTER_CATEGORIES = [
    {"slug": "phones", "name": "Phones", "display_order": 0},
    {"slug": "laptops", "name": "Laptops", "display_order": 1},
    {"slug": "audio", "name": "Audio", "display_order": 2},
    {"slug": "tablets", "name": "Tablets", "display_order": 3},
    {"slug": "cameras", "name": "Cameras", "display_order": 4},
    {"slug": "accessories", "name": "Accessories", "display_order": 5},
]


class Command(BaseCommand):
    help = (
        "Idempotently load all 6 marketplace categories matching the frontend taxonomy."
    )

    def handle(self, *args, **options):
        for data in STARTER_CATEGORIES:
            category, created = Category.objects.update_or_create(
                slug=data["slug"],
                defaults={
                    "name": data["name"],
                    "display_order": data["display_order"],
                },
            )
            self.stdout.write(
                f"{'Created' if created else 'Updated'}: {category.name} ({category.slug})"
            )
