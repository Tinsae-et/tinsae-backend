from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from catalog.models import Category, Product
from shops.models import Shop
from users.models import User

# How far back to set the confirmation clock for each design-spec label. The
# sweep falls back to created_at when last_confirmed_at is NULL, so leaving the
# older labels unset would seed everything as fresh and leave nothing to sweep.
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

PRODUCTS = [
    # Shewa Electronics — Phones
    {
        "shop": 0,
        "category_slug": "phones",
        "brand": "Samsung",
        "name": "Galaxy A15",
        "price": "12500.00",
        "freshness": "fresh",
        "specs": {"ram": "4GB", "storage": "128GB"},
    },
    {
        "shop": 0,
        "category_slug": "phones",
        "brand": "Samsung",
        "name": "Galaxy A25",
        "price": "16500.00",
        "freshness": "fresh",
        "specs": {"ram": "6GB", "storage": "128GB"},
    },
    {
        "shop": 0,
        "category_slug": "phones",
        "brand": "Tecno",
        "name": "Camon 20",
        "price": "9500.00",
        "freshness": "almost_expired",
        "specs": {"ram": "4GB", "storage": "128GB"},
    },
    {
        "shop": 0,
        "category_slug": "phones",
        "brand": "Infinix",
        "name": "Hot 40",
        "price": "7200.00",
        "freshness": "fresh",
        "specs": {"ram": "4GB", "storage": "64GB"},
    },
    {
        "shop": 0,
        "category_slug": "phones",
        "brand": "Nokia",
        "name": "105 Classic",
        "price": "1800.00",
        "freshness": "expired",
        "specs": {},
    },
    {
        "shop": 0,
        "category_slug": "phones",
        "brand": "iPhone",
        "name": "13 128GB",
        "price": "52000.00",
        "freshness": "fresh",
        "specs": {"storage": "128GB"},
    },
    {
        "shop": 0,
        "category_slug": "phones",
        "brand": "iPhone",
        "name": "11 64GB",
        "price": "34000.00",
        "freshness": "almost_expired",
        "specs": {"storage": "64GB"},
    },
    {
        "shop": 0,
        "category_slug": "phones",
        "brand": "Xiaomi",
        "name": "Redmi Note 13",
        "price": "14000.00",
        "freshness": "fresh",
        "specs": {"ram": "6GB", "storage": "128GB"},
    },
    {
        "shop": 0,
        "category_slug": "phones",
        "brand": "Oppo",
        "name": "A78",
        "price": "13500.00",
        "freshness": "fresh",
        "specs": {"ram": "4GB", "storage": "128GB"},
    },
    {
        "shop": 0,
        "category_slug": "phones",
        "brand": "Huawei",
        "name": "Y9 Pro",
        "price": "11000.00",
        "freshness": "almost_expired",
        "specs": {"ram": "4GB", "storage": "64GB"},
    },
    # Shewa Electronics — Accessories
    {
        "shop": 0,
        "category_slug": "accessories",
        "brand": "Anker",
        "name": "PowerBank 20000mAh",
        "price": "2500.00",
        "freshness": "fresh",
        "specs": {"capacity": "20000mAh"},
    },
    {
        "shop": 0,
        "category_slug": "accessories",
        "brand": "Baseus",
        "name": "USB-C Cable 2m",
        "price": "450.00",
        "freshness": "fresh",
        "specs": {"length": "2m"},
    },
    # Merkato Electronics — Phones
    {
        "shop": 1,
        "category_slug": "phones",
        "brand": "Samsung",
        "name": "Galaxy S24",
        "price": "78500.00",
        "freshness": "fresh",
        "specs": {"ram": "8GB", "storage": "256GB"},
    },
    {
        "shop": 1,
        "category_slug": "phones",
        "brand": "Samsung",
        "name": "Galaxy Z Flip 5",
        "price": "92000.00",
        "freshness": "fresh",
        "specs": {"storage": "256GB"},
    },
    {
        "shop": 1,
        "category_slug": "phones",
        "brand": "iPhone",
        "name": "15 Pro 256GB",
        "price": "115000.00",
        "freshness": "fresh",
        "specs": {"storage": "256GB"},
    },
    {
        "shop": 1,
        "category_slug": "phones",
        "brand": "iPhone",
        "name": "14 128GB",
        "price": "72000.00",
        "freshness": "almost_expired",
        "specs": {"storage": "128GB"},
    },
    {
        "shop": 1,
        "category_slug": "phones",
        "brand": "Tecno",
        "name": "Spark 20",
        "price": "8500.00",
        "freshness": "fresh",
        "specs": {"ram": "4GB", "storage": "128GB"},
    },
    {
        "shop": 1,
        "category_slug": "phones",
        "brand": "Xiaomi",
        "name": "Redmi 13C",
        "price": "7800.00",
        "freshness": "almost_expired",
        "specs": {"ram": "4GB", "storage": "64GB"},
    },
    {
        "shop": 1,
        "category_slug": "phones",
        "brand": "OnePlus",
        "name": "Nord CE 3",
        "price": "22000.00",
        "freshness": "fresh",
        "specs": {"ram": "8GB", "storage": "128GB"},
    },
    {
        "shop": 1,
        "category_slug": "phones",
        "brand": "Google",
        "name": "Pixel 8",
        "price": "68000.00",
        "freshness": "fresh",
        "specs": {"storage": "128GB"},
    },
    # Merkato Electronics — Accessories
    {
        "shop": 1,
        "category_slug": "accessories",
        "brand": "JBL",
        "name": "Tune 510BT",
        "price": "3200.00",
        "freshness": "fresh",
        "specs": {"type": "over-ear", "battery": "40h"},
    },
    {
        "shop": 1,
        "category_slug": "accessories",
        "brand": "Anker",
        "name": "Nano Charger 65W",
        "price": "1800.00",
        "freshness": "fresh",
        "specs": {"power": "65W"},
    },
    {
        "shop": 1,
        "category_slug": "accessories",
        "brand": "Spigen",
        "name": "Galaxy S24 Case",
        "price": "650.00",
        "freshness": "almost_expired",
        "specs": {"material": "silicone"},
    },
    {
        "shop": 1,
        "category_slug": "accessories",
        "brand": "Belkin",
        "name": "Screen Protector iPhone 15",
        "price": "850.00",
        "freshness": "fresh",
        "specs": {"type": "tempered glass"},
    },
    {
        "shop": 1,
        "category_slug": "accessories",
        "brand": "Xiaomi",
        "name": "Mi Band 8",
        "price": "2900.00",
        "freshness": "expired",
        "specs": {"display": "AMOLED"},
    },
    # Bahir Dar Tech — Accessories
    {
        "shop": 2,
        "category_slug": "accessories",
        "brand": "Oraimo",
        "name": "PowerBank 10000mAh",
        "price": "1200.00",
        "freshness": "fresh",
        "specs": {"capacity": "10000mAh"},
    },
    {
        "shop": 2,
        "category_slug": "accessories",
        "brand": "Oraimo",
        "name": "Bluetooth Speaker",
        "price": "1800.00",
        "freshness": "fresh",
        "specs": {"battery": "12h"},
    },
    {
        "shop": 2,
        "category_slug": "accessories",
        "brand": "Baseus",
        "name": "Car Charger 30W",
        "price": "700.00",
        "freshness": "almost_expired",
        "specs": {"power": "30W"},
    },
    # Bahir Dar Tech — Phones
    {
        "shop": 2,
        "category_slug": "phones",
        "brand": "Tecno",
        "name": "Pova 5",
        "price": "10500.00",
        "freshness": "fresh",
        "specs": {"ram": "8GB", "storage": "128GB"},
    },
    {
        "shop": 2,
        "category_slug": "phones",
        "brand": "Infinix",
        "name": "Note 30",
        "price": "9800.00",
        "freshness": "fresh",
        "specs": {"ram": "6GB", "storage": "128GB"},
    },
    {
        "shop": 2,
        "category_slug": "phones",
        "brand": "Nokia",
        "name": "G22",
        "price": "6500.00",
        "freshness": "expired",
        "specs": {"ram": "4GB", "storage": "64GB"},
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
        for data in PRODUCTS:
            shop = shop_objs[data["shop"]]
            category = Category.objects.get(slug=data["category_slug"])
            slug = f"{shop.slug}-{data['brand']}-{data['name']}".lower().replace(
                " ", "-"
            )
            product, created = Product.objects.get_or_create(
                slug=slug,
                defaults={
                    "shop": shop,
                    "category": category,
                    "brand": data["brand"],
                    "name": data["name"],
                    "price": data["price"],
                    "specs": data["specs"],
                    "last_confirmed_at": timezone.now()
                    - SEED_CONFIRMATION_AGE[data.get("freshness", "fresh")],
                },
            )
            if created:
                count += 1
        self.stdout.write(
            f"Created {count} new products ({Product.objects.count()} total)"
        )
