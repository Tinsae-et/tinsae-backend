from django.core.management.base import BaseCommand

from catalog.models import Category
from catalog.seed_specs import ensure_taxonomy

# A parent is listed before its children.
STARTER_CATEGORIES = [
    # Electronics in the groups buyers shop by. The original slugs (phones,
    # laptops...) are unchanged, so every existing link keeps working.
    {
        "slug": "phones-tablets",
        "icon": "phone",
        "tagline": "Phones · Tablets · Smartwatches",
        "name": "Phones & Tablets",
        "display_order": 0,
    },
    {
        "slug": "phones",
        "icon": "phone",
        "tagline": "iPhone · Samsung · Tecno · Xiaomi",
        "name": "Phones",
        "display_order": 0,
        "parent": "phones-tablets",
    },
    {
        "slug": "tablets",
        "icon": "tablet",
        "tagline": "iPad · Galaxy Tab · Surface",
        "name": "Tablets",
        "display_order": 1,
        "parent": "phones-tablets",
    },
    {
        "slug": "wearables",
        "icon": "watch",
        "tagline": "Apple Watch · Galaxy Watch · Bands",
        "name": "Smartwatches & Wearables",
        "display_order": 2,
        "parent": "phones-tablets",
    },
    {
        "slug": "computers",
        "icon": "laptop",
        "tagline": "Laptops · Desktops · Monitors",
        "name": "Computers",
        "display_order": 1,
    },
    {
        "slug": "laptops",
        "icon": "laptop",
        "tagline": "MacBook · Dell · ThinkPad · HP",
        "name": "Laptops",
        "display_order": 0,
        "parent": "computers",
    },
    {
        "slug": "desktops",
        "icon": "desktop",
        "tagline": "Towers · All-in-ones · Mini PCs",
        "name": "Desktops",
        "display_order": 1,
        "parent": "computers",
    },
    {
        "slug": "monitors",
        "icon": "monitor",
        "tagline": "Samsung · LG · Dell · AOC",
        "name": "Monitors",
        "display_order": 2,
        "parent": "computers",
    },
    {
        "slug": "tv-audio",
        "icon": "tv",
        "tagline": "TVs · Headphones · Speakers",
        "name": "TV & Audio",
        "display_order": 2,
    },
    {
        "slug": "tvs",
        "icon": "tv",
        "tagline": "Samsung · LG · Hisense · TCL",
        "name": "TVs",
        "display_order": 0,
        "parent": "tv-audio",
    },
    {
        "slug": "audio",
        "icon": "audio",
        "tagline": "AirPods · JBL · Sony · Soundbars",
        "name": "Audio",
        "display_order": 1,
        "parent": "tv-audio",
    },
    {
        "slug": "cameras",
        "icon": "camera",
        "tagline": "Canon · Sony · Nikon · GoPro",
        "name": "Cameras",
        "display_order": 3,
    },
    {
        "slug": "accessories",
        "icon": "plug",
        "tagline": "Chargers · Cases · Cables · Power",
        "name": "Accessories",
        "display_order": 4,
    },
    {
        "slug": "vehicles",
        "icon": "car",
        "tagline": "Cars · Motorcycles · Bicycles · Bajaj · Trucks",
        "name": "Vehicles & Auto",
        "display_order": 5,
    },
    {
        "slug": "cars",
        "icon": "car",
        "tagline": "Toyota · Suzuki · Hyundai · BYD",
        "name": "Cars",
        "display_order": 0,
        "parent": "vehicles",
    },
    # "Motorcycles & Scooters" and "Bicycles & E-bikes" rather than "bikes":
    # side by side, the names say which has an engine.
    {
        "slug": "motorcycles",
        "icon": "motorbike",
        "tagline": "Bajaj · TVS · Honda · Yamaha",
        "name": "Motorcycles & Scooters",
        "display_order": 1,
        "parent": "vehicles",
    },
    {
        "slug": "bicycles",
        "icon": "bicycle",
        "tagline": "Mountain · City · Kids · Electric",
        "name": "Bicycles & E-bikes",
        "display_order": 2,
        "parent": "vehicles",
    },
    {
        "slug": "three-wheelers",
        "icon": "car",
        "tagline": "Bajaj RE · TVS King · Piaggio",
        "name": "Three-wheelers",
        "display_order": 3,
        "parent": "vehicles",
    },
    {
        "slug": "trucks-heavy",
        "icon": "truck",
        "tagline": "Trucks · Buses · Minibuses · Machinery",
        "name": "Trucks & Heavy",
        "display_order": 4,
        "parent": "vehicles",
    },
    {
        "slug": "auto-parts",
        "icon": "box",
        "tagline": "Tyres · Batteries · Engine parts",
        "name": "Parts & Accessories",
        "display_order": 5,
        "parent": "vehicles",
    },
    {
        "slug": "furniture-appliances",
        "icon": "sofa",
        "tagline": "Furniture · Beds · Appliances · Kitchen",
        "name": "Furniture & Appliances",
        "display_order": 6,
    },
    {
        "slug": "furniture",
        "icon": "sofa",
        "tagline": "Sofas · Tables · Wardrobes · Desks",
        "name": "Furniture",
        "display_order": 0,
        "parent": "furniture-appliances",
    },
    {
        "slug": "beds-mattresses",
        "icon": "bed",
        "tagline": "Bed frames · Mattresses · Bedding",
        "name": "Beds & Mattresses",
        "display_order": 1,
        "parent": "furniture-appliances",
    },
    {
        "slug": "appliances",
        "icon": "fridge",
        "tagline": "Fridges · Washers · Stoves · ACs",
        "name": "Appliances",
        "display_order": 2,
        "parent": "furniture-appliances",
    },
    {
        "slug": "kitchen-dining",
        "icon": "pan",
        "tagline": "Cookware · Electric mitad · Blenders",
        "name": "Kitchen & Dining",
        "display_order": 3,
        "parent": "furniture-appliances",
    },
    {
        "slug": "decor-lighting",
        "icon": "lamp",
        "tagline": "Lamps · Rugs · Curtains · Mirrors",
        "name": "Décor & Lighting",
        "display_order": 4,
        "parent": "furniture-appliances",
    },
    {
        "slug": "power-solar",
        "icon": "bolt",
        "tagline": "Generators · Solar · Inverters · Batteries",
        "name": "Power & Solar",
        "display_order": 7,
    },
    {
        "slug": "generators",
        "icon": "generator",
        "tagline": "Petrol · Diesel · Silent · Three-phase",
        "name": "Generators",
        "display_order": 0,
        "parent": "power-solar",
    },
    {
        "slug": "solar",
        "icon": "sun",
        "tagline": "Panels · Home kits · Solar lights",
        "name": "Solar Panels & Kits",
        "display_order": 1,
        "parent": "power-solar",
    },
    {
        "slug": "inverters-batteries",
        "icon": "battery",
        "tagline": "Inverters · UPS · Lithium · Stabilizers",
        "name": "Inverters & Batteries",
        "display_order": 2,
        "parent": "power-solar",
    },
]


def ensure_categories() -> list[tuple[Category, bool]]:
    """Create or update every starter category; (category, created) per row."""
    rows = []
    by_slug = {}
    for data in STARTER_CATEGORIES:
        category, created = Category.objects.update_or_create(
            slug=data["slug"],
            defaults={
                "name": data["name"],
                "display_order": data["display_order"],
                "parent": by_slug.get(data.get("parent")),
                "icon": data.get("icon", ""),
                "tagline": data.get("tagline", ""),
            },
        )
        by_slug[category.slug] = category
        rows.append((category, created))
    return rows


class Command(BaseCommand):
    help = "Idempotently load the marketplace categories and link their spec schemas."

    def handle(self, *args, **options):
        for category, created in ensure_categories():
            self.stdout.write(
                f"{'Created' if created else 'Updated'}: {category.name} ({category.slug})"
            )
        # Categories without their product types would take free-form specs;
        # link them now so a fresh database is complete after this command.
        ensure_taxonomy()
        self.stdout.write("Taxonomy synced.")
