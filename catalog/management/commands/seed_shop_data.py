from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from catalog.models import Category, Product
from shops.models import Shop
from users.models import User

# How far back to set the confirmation clock for each design-spec label. Every
# seeded product gets a real timestamp: the sweep treats a NULL last_confirmed_at
# as stale outright, so leaving any of these unset would seed it as stale
# regardless of the label.
SEED_CONFIRMATION_AGE = {
    "fresh": timedelta(hours=2),
    "almost_expired": timedelta(days=25),
    "expired": timedelta(days=45),
}

SEED_USER = {
    "username": "seed_merchant",
    "email": "merchant@tinsae.et",
    "password": "seedpass123",
}

SHOPS = [
    {
        "name": "Shewa Electronics",
        "slug": "shewa-electronics",
        "city": "Addis Ababa",
        "phone": "+251911100001",
        "description": "Phones, chargers, and accessories in Bole.",
    },
    {
        "name": "Merkato Electronics",
        "slug": "merkato-electronics",
        "city": "Addis Ababa",
        "phone": "+251911100002",
        "description": "Premium phones and gadgets at Merkato.",
    },
    {
        "name": "Bahir Dar Tech",
        "slug": "bahir-dar-tech",
        "city": "Bahir Dar",
        "phone": "+251911100003",
        "description": "Electronics and phone accessories in Bahir Dar.",
    },
]

# Full per-category spec sets, matching the design's "Key specifications"
# (design_handoff_listing): phones carry Storage/Display/Chip/Camera/Battery/
# 5G/Year/Weight/SIM; accessories carry their own keys. `description` is the
# variant line shown under the h1 (e.g. "Natural Titanium").
PRODUCTS = [
    # Shewa Electronics — Phones
    {
        "shop": 0,
        "category_slug": "phones",
        "brand": "Samsung",
        "name": "Galaxy A15",
        "description": "Light Blue",
        "price": "12500.00",
        "freshness": "fresh",
        "specs": {
            "storage": "128GB",
            "display": "6.5″ AMOLED",
            "chip": "Helio G99",
            "camera": "50MP triple",
            "battery": "5,000 mAh",
            "ram": "4GB",
            "n5g": "No",
            "year": "2023",
            "weight": "200 g",
            "sim": "Nano",
        },
    },
    {
        "shop": 0,
        "category_slug": "phones",
        "brand": "Samsung",
        "name": "Galaxy A25",
        "description": "Blue Black",
        "price": "16500.00",
        "freshness": "fresh",
        "specs": {
            "storage": "128GB",
            "display": "6.5″ AMOLED",
            "chip": "Exynos 1280",
            "camera": "50MP triple",
            "battery": "5,000 mAh",
            "ram": "6GB",
            "n5g": "Yes",
            "year": "2023",
            "weight": "197 g",
            "sim": "Nano",
        },
    },
    {
        "shop": 0,
        "category_slug": "phones",
        "brand": "Tecno",
        "name": "Camon 20",
        "description": "Serenity Blue",
        "price": "9500.00",
        "freshness": "almost_expired",
        "specs": {
            "storage": "128GB",
            "display": "6.67″ AMOLED",
            "chip": "Dimensity 810",
            "camera": "64MP triple",
            "battery": "5,000 mAh",
            "ram": "4GB",
            "n5g": "No",
            "year": "2023",
            "weight": "190 g",
            "sim": "Dual Nano",
        },
    },
    {
        "shop": 0,
        "category_slug": "phones",
        "brand": "Infinix",
        "name": "Hot 40",
        "description": "Palm Blue",
        "price": "7200.00",
        "freshness": "fresh",
        "specs": {
            "storage": "64GB",
            "display": "6.78″ IPS LCD",
            "chip": "Helio G88",
            "camera": "50MP dual",
            "battery": "5,000 mAh",
            "ram": "4GB",
            "n5g": "No",
            "year": "2023",
            "weight": "199 g",
            "sim": "Dual Nano",
        },
    },
    {
        "shop": 0,
        "category_slug": "phones",
        "brand": "Nokia",
        "name": "105 Classic",
        "description": "Dark Blue",
        "price": "1800.00",
        "freshness": "expired",
        "specs": {
            "display": "1.8″ QVGA",
            "battery": "1,450 mAh",
            "n5g": "No",
            "year": "2023",
            "weight": "70 g",
            "sim": "Nano",
        },
    },
    {
        "shop": 0,
        "category_slug": "phones",
        "brand": "iPhone",
        "name": "13 128GB",
        "description": "Starlight",
        "price": "52000.00",
        "freshness": "fresh",
        "specs": {
            "storage": "128GB",
            "display": "6.1″ OLED",
            "chip": "A15 Bionic",
            "camera": "12MP dual",
            "battery": "3,240 mAh",
            "n5g": "Yes",
            "year": "2021",
            "weight": "174 g",
            "sim": "Nano + eSIM",
        },
    },
    {
        "shop": 0,
        "category_slug": "phones",
        "brand": "iPhone",
        "name": "11 64GB",
        "description": "Purple",
        "price": "34000.00",
        "freshness": "almost_expired",
        "specs": {
            "storage": "64GB",
            "display": "6.1″ Liquid Retina",
            "chip": "A13 Bionic",
            "camera": "12MP dual",
            "battery": "3,110 mAh",
            "n5g": "No",
            "year": "2019",
            "weight": "194 g",
            "sim": "Nano + eSIM",
        },
    },
    {
        "shop": 0,
        "category_slug": "phones",
        "brand": "Xiaomi",
        "name": "Redmi Note 13",
        "description": "Midnight Black",
        "price": "14000.00",
        "freshness": "fresh",
        "specs": {
            "storage": "128GB",
            "display": "6.67″ AMOLED",
            "chip": "Snapdragon 685",
            "camera": "108MP triple",
            "battery": "5,000 mAh",
            "ram": "6GB",
            "n5g": "No",
            "year": "2024",
            "weight": "189 g",
            "sim": "Dual Nano",
        },
    },
    {
        "shop": 0,
        "category_slug": "phones",
        "brand": "Oppo",
        "name": "A78",
        "description": "Aqua Green",
        "price": "13500.00",
        "freshness": "fresh",
        "specs": {
            "storage": "128GB",
            "display": "6.43″ AMOLED",
            "chip": "Dimensity 700",
            "camera": "50MP dual",
            "battery": "5,000 mAh",
            "ram": "4GB",
            "n5g": "Yes",
            "year": "2023",
            "weight": "180 g",
            "sim": "Dual Nano",
        },
    },
    {
        "shop": 0,
        "category_slug": "phones",
        "brand": "Huawei",
        "name": "Y9 Pro",
        "description": "Midnight Black",
        "price": "11000.00",
        "freshness": "almost_expired",
        "specs": {
            "storage": "64GB",
            "display": "6.59″ IPS LCD",
            "chip": "Helio P35",
            "camera": "48MP quad",
            "battery": "4,000 mAh",
            "ram": "4GB",
            "n5g": "No",
            "year": "2020",
            "weight": "197 g",
            "sim": "Nano",
        },
    },
    # Shewa Electronics — Accessories
    {
        "shop": 0,
        "category_slug": "accessories",
        "brand": "Anker",
        "name": "PowerBank 20000mAh",
        "description": "20000mAh, dual USB",
        "price": "2500.00",
        "freshness": "fresh",
        "specs": {
            "capacity": "20000mAh",
            "type": "Power bank",
            "power": "22.5W",
            "port": "USB-C + USB-A",
            "material": "Plastic",
        },
    },
    {
        "shop": 0,
        "category_slug": "accessories",
        "brand": "Baseus",
        "name": "USB-C Cable 2m",
        "description": "USB-C to USB-C, braided",
        "price": "450.00",
        "freshness": "fresh",
        "specs": {
            "length": "2m",
            "type": "USB-C to USB-C",
            "power": "100W",
            "material": "Nylon braided",
        },
    },
    # Merkato Electronics — Phones
    {
        "shop": 1,
        "category_slug": "phones",
        "brand": "Samsung",
        "name": "Galaxy S24",
        "description": "Onyx Black",
        "price": "78500.00",
        "freshness": "fresh",
        "specs": {
            "storage": "256GB",
            "display": "6.2″ Dynamic AMOLED",
            "chip": "Exynos 2400",
            "camera": "50MP triple",
            "battery": "4,000 mAh",
            "ram": "8GB",
            "n5g": "Yes",
            "year": "2024",
            "weight": "168 g",
            "sim": "Nano + eSIM",
        },
    },
    {
        "shop": 1,
        "category_slug": "phones",
        "brand": "Samsung",
        "name": "Galaxy Z Flip 5",
        "description": "Mint",
        "price": "92000.00",
        "freshness": "fresh",
        "specs": {
            "storage": "256GB",
            "display": "6.7″ Foldable AMOLED",
            "chip": "Snapdragon 8 Gen 2",
            "camera": "12MP dual",
            "battery": "3,700 mAh",
            "ram": "8GB",
            "n5g": "Yes",
            "year": "2023",
            "weight": "187 g",
            "sim": "Nano + eSIM",
        },
    },
    {
        "shop": 1,
        "category_slug": "phones",
        "brand": "iPhone",
        "name": "15 Pro 256GB",
        "description": "Natural Titanium",
        "price": "115000.00",
        "freshness": "fresh",
        "specs": {
            "storage": "256GB",
            "display": "6.1″ OLED",
            "chip": "A17 Pro",
            "camera": "48MP triple",
            "battery": "3,274 mAh",
            "n5g": "Yes",
            "year": "2023",
            "weight": "187 g",
            "sim": "Nano + eSIM",
        },
    },
    {
        "shop": 1,
        "category_slug": "phones",
        "brand": "iPhone",
        "name": "14 128GB",
        "description": "Midnight",
        "price": "72000.00",
        "freshness": "almost_expired",
        "specs": {
            "storage": "128GB",
            "display": "6.1″ OLED",
            "chip": "A15 Bionic",
            "camera": "12MP dual",
            "battery": "3,279 mAh",
            "n5g": "Yes",
            "year": "2022",
            "weight": "172 g",
            "sim": "Nano + eSIM",
        },
    },
    {
        "shop": 1,
        "category_slug": "phones",
        "brand": "Tecno",
        "name": "Spark 20",
        "description": "Gravity Black",
        "price": "8500.00",
        "freshness": "fresh",
        "specs": {
            "storage": "128GB",
            "display": "6.6″ IPS LCD",
            "chip": "Helio G85",
            "camera": "50MP dual",
            "battery": "5,000 mAh",
            "ram": "4GB",
            "n5g": "No",
            "year": "2023",
            "weight": "190 g",
            "sim": "Dual Nano",
        },
    },
    {
        "shop": 1,
        "category_slug": "phones",
        "brand": "Xiaomi",
        "name": "Redmi 13C",
        "description": "Midnight Black",
        "price": "7800.00",
        "freshness": "almost_expired",
        "specs": {
            "storage": "64GB",
            "display": "6.74″ IPS LCD",
            "chip": "Helio G85",
            "camera": "50MP triple",
            "battery": "5,000 mAh",
            "ram": "4GB",
            "n5g": "No",
            "year": "2023",
            "weight": "192 g",
            "sim": "Dual Nano",
        },
    },
    {
        "shop": 1,
        "category_slug": "phones",
        "brand": "OnePlus",
        "name": "Nord CE 3",
        "description": "Aqua Surge",
        "price": "22000.00",
        "freshness": "fresh",
        "specs": {
            "storage": "128GB",
            "display": "6.7″ AMOLED",
            "chip": "Snapdragon 782G",
            "camera": "50MP triple",
            "battery": "5,000 mAh",
            "ram": "8GB",
            "n5g": "Yes",
            "year": "2023",
            "weight": "184 g",
            "sim": "Dual Nano",
        },
    },
    {
        "shop": 1,
        "category_slug": "phones",
        "brand": "Google",
        "name": "Pixel 8",
        "description": "Obsidian",
        "price": "68000.00",
        "freshness": "fresh",
        "specs": {
            "storage": "128GB",
            "display": "6.2″ OLED",
            "chip": "Tensor G3",
            "camera": "50MP dual",
            "battery": "4,575 mAh",
            "ram": "8GB",
            "n5g": "Yes",
            "year": "2023",
            "weight": "187 g",
            "sim": "Nano + eSIM",
        },
    },
    # Merkato Electronics — Accessories
    {
        "shop": 1,
        "category_slug": "accessories",
        "brand": "JBL",
        "name": "Tune 510BT",
        "description": "Blue",
        "price": "3200.00",
        "freshness": "fresh",
        "specs": {
            "type": "Over-ear",
            "battery": "40h",
            "driver": "32mm",
            "bluetooth": "5.0",
            "weight": "160 g",
        },
    },
    {
        "shop": 1,
        "category_slug": "accessories",
        "brand": "Anker",
        "name": "Nano Charger 65W",
        "description": "GaN, USB-C",
        "price": "1800.00",
        "freshness": "fresh",
        "specs": {
            "power": "65W",
            "type": "GaN wall charger",
            "port": "USB-C",
        },
    },
    {
        "shop": 1,
        "category_slug": "accessories",
        "brand": "Spigen",
        "name": "Galaxy S24 Case",
        "description": "Clear",
        "price": "650.00",
        "freshness": "almost_expired",
        "specs": {
            "material": "Silicone",
            "type": "Case",
        },
    },
    {
        "shop": 1,
        "category_slug": "accessories",
        "brand": "Belkin",
        "name": "Screen Protector iPhone 15",
        "description": "Tempered glass, 2-pack",
        "price": "850.00",
        "freshness": "fresh",
        "specs": {
            "type": "Tempered glass",
            "material": "Glass",
        },
    },
    {
        "shop": 1,
        "category_slug": "accessories",
        "brand": "Xiaomi",
        "name": "Mi Band 8",
        "description": "Midnight Black",
        "price": "2900.00",
        "freshness": "expired",
        "specs": {
            "display": "1.62″ AMOLED",
            "battery": "190 mAh",
            "weight": "27 g",
            "water_resistance": "5ATM",
        },
    },
    # Bahir Dar Tech — Accessories
    {
        "shop": 2,
        "category_slug": "accessories",
        "brand": "Oraimo",
        "name": "PowerBank 10000mAh",
        "description": "10000mAh, slim",
        "price": "1200.00",
        "freshness": "fresh",
        "specs": {
            "capacity": "10000mAh",
            "type": "Power bank",
            "port": "USB-C + USB-A",
        },
    },
    {
        "shop": 2,
        "category_slug": "accessories",
        "brand": "Oraimo",
        "name": "Bluetooth Speaker",
        "description": "Portable, 5W",
        "price": "1800.00",
        "freshness": "fresh",
        "specs": {
            "battery": "12h",
            "power": "5W",
            "bluetooth": "5.0",
            "type": "Portable speaker",
        },
    },
    {
        "shop": 2,
        "category_slug": "accessories",
        "brand": "Baseus",
        "name": "Car Charger 30W",
        "description": "Dual USB-C",
        "price": "700.00",
        "freshness": "almost_expired",
        "specs": {
            "power": "30W",
            "type": "Car charger",
            "port": "USB-C x2",
        },
    },
    # Bahir Dar Tech — Phones
    {
        "shop": 2,
        "category_slug": "phones",
        "brand": "Tecno",
        "name": "Pova 5",
        "description": "Aurora Blue",
        "price": "10500.00",
        "freshness": "fresh",
        "specs": {
            "storage": "128GB",
            "display": "6.8″ IPS LCD",
            "chip": "Helio G99",
            "camera": "50MP dual",
            "battery": "6,000 mAh",
            "ram": "8GB",
            "n5g": "No",
            "year": "2023",
            "weight": "220 g",
            "sim": "Dual Nano",
        },
    },
    {
        "shop": 2,
        "category_slug": "phones",
        "brand": "Infinix",
        "name": "Note 30",
        "description": "Interstellar Blue",
        "price": "9800.00",
        "freshness": "fresh",
        "specs": {
            "storage": "128GB",
            "display": "6.78″ AMOLED",
            "chip": "Helio G99",
            "camera": "64MP triple",
            "battery": "5,000 mAh",
            "ram": "6GB",
            "n5g": "No",
            "year": "2023",
            "weight": "204 g",
            "sim": "Dual Nano",
        },
    },
    {
        "shop": 2,
        "category_slug": "phones",
        "brand": "Nokia",
        "name": "G22",
        "description": "Meteor Gray",
        "price": "6500.00",
        "freshness": "expired",
        "specs": {
            "storage": "64GB",
            "display": "6.5″ IPS LCD",
            "chip": "Unisoc T606",
            "camera": "50MP triple",
            "battery": "5,050 mAh",
            "ram": "4GB",
            "n5g": "No",
            "year": "2023",
            "weight": "196 g",
            "sim": "Dual Nano",
        },
    },
]


class Command(BaseCommand):
    help = "Idempotently seed shops, categories, and products for development."

    def handle(self, *args, **options):
        user, created = User.objects.get_or_create(
            username=SEED_USER["username"],
            defaults={"email": SEED_USER["email"]},
        )
        if created:
            user.set_password(SEED_USER["password"])
            user.save()
            self.stdout.write(f"Created seed user: {user.username}")
        else:
            self.stdout.write(f"Exists seed user: {user.username}")

        shop_objs = []
        for data in SHOPS:
            shop, created = Shop.objects.get_or_create(
                slug=data["slug"],
                defaults={
                    "name": data["name"],
                    "city": data["city"],
                    "phone": data["phone"],
                    "description": data["description"],
                    "owner": user,
                },
            )
            shop_objs.append(shop)
            self.stdout.write(f"{'Created' if created else 'Exists'}: Shop {shop.name}")

        count = 0
        updated = 0
        for data in PRODUCTS:
            shop = shop_objs[data["shop"]]
            category = Category.objects.get(slug=data["category_slug"])
            slug = f"{shop.slug}-{data['brand']}-{data['name']}".lower().replace(
                " ", "-"
            )
            product, created = Product.objects.update_or_create(
                slug=slug,
                defaults={
                    "shop": shop,
                    "category": category,
                    "brand": data["brand"],
                    "name": data["name"],
                    "description": data.get("description", ""),
                    "price": data["price"],
                    "specs": data["specs"],
                    "last_confirmed_at": timezone.now()
                    - SEED_CONFIRMATION_AGE[data.get("freshness", "fresh")],
                },
            )
            if created:
                count += 1
            else:
                updated += 1
        self.stdout.write(
            f"Created {count} new products, updated {updated} "
            f"({Product.objects.count()} total)"
        )
