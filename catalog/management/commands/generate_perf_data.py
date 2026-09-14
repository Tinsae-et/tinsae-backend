"""Management command to generate realistic performance and load-testing datasets.

Matches Section 4 (Principle 3) & Section 13 of the Marketplace Load Testing Guide:
Populates realistic volume of categories, shops, and products for PostgreSQL
trigram fuzzy search (pg_trgm) and GIN index benchmarking.
"""

import random
from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.utils import timezone
from django.utils.text import slugify

from catalog.models import (
    Category,
    ConfirmationStatus,
    Product,
    ProductCondition,
    ProductStatus,
)
from shops.models import Shop

User = get_user_model()

CATEGORIES = [
    {"slug": "phones", "name": "Phones", "display_order": 0},
    {"slug": "laptops", "name": "Laptops", "display_order": 1},
    {"slug": "audio", "name": "Audio", "display_order": 2},
    {"slug": "tablets", "name": "Tablets", "display_order": 3},
    {"slug": "cameras", "name": "Cameras", "display_order": 4},
    {"slug": "accessories", "name": "Accessories", "display_order": 5},
    {"slug": "vehicles", "name": "Vehicles & Auto", "display_order": 6},
    {"slug": "electronics", "name": "Consumer Electronics", "display_order": 7},
]

CITIES = [
    "Addis Ababa",
    "Bole",
    "Piazza",
    "CMC",
    "Megenagna",
    "Sarbet",
    "Hawassa",
    "Bahir Dar",
    "Adama",
    "Dire Dawa",
]

PRODUCT_TEMPLATES = [
    # Phones
    ("Apple", "iPhone 15 Pro Max 256GB Titanium", "phones", 145000, 185000),
    ("Apple", "iPhone 14 Pro 128GB Deep Purple", "phones", 110000, 135000),
    ("Apple", "iPhone 13 128GB Midnight", "phones", 78000, 92000),
    ("Apple", "iPhone 12 64GB Blue Clean", "phones", 52000, 64000),
    ("Apple", "iPhone 11 128GB Black", "phones", 42000, 50000),
    ("Samsung", "Galaxy S24 Ultra 512GB Gray", "phones", 160000, 195000),
    ("Samsung", "Galaxy S23 256GB Phantom Black", "phones", 85000, 105000),
    ("Samsung", "Galaxy A54 5G 128GB Awesome Lime", "phones", 38000, 48000),
    ("Samsung", "Galaxy A14 64GB Dual SIM", "phones", 19000, 24000),
    ("Tecno", "Camon 20 Premier 5G 512GB", "phones", 39000, 46000),
    ("Tecno", "Spark 10 Pro 256GB Starry Black", "phones", 21000, 26000),
    ("Infinix", "Note 30 Pro 256GB Variable Gold", "phones", 27000, 34000),
    ("Xiaomi", "Redmi Note 13 Pro+ 5G 256GB", "phones", 42000, 51000),
    ("Google", "Pixel 8 Pro 128GB Bay Blue", "phones", 95000, 120000),
    ("Google", "Pixel 7a 128GB Charcoal", "phones", 54000, 65000),
    # Laptops
    ("Apple", "MacBook Pro 14 M3 16GB 512GB Space Black", "laptops", 210000, 260000),
    ("Apple", "MacBook Air 13 M2 8GB 256GB Midnight", "laptops", 115000, 140000),
    ("Apple", "MacBook Pro 16 M1 Pro 16GB 512GB", "laptops", 145000, 175000),
    ("Dell", "XPS 13 Plus 9320 Core i7 16GB 512GB SSD", "laptops", 125000, 155000),
    ("Dell", "Latitude 5420 Core i5 11th Gen 16GB 256GB", "laptops", 48000, 58000),
    ("Dell", "Precision 5560 Mobile Workstation RTX A2000", "laptops", 130000, 160000),
    ("HP", "EliteBook 840 G8 Core i7 16GB 512GB", "laptops", 62000, 78000),
    ("HP", "Pavilion 15 Core i5 12th Gen 8GB 512GB", "laptops", 52000, 63000),
    ("HP", "Spectre x360 2-in-1 14 OLED Touch", "laptops", 140000, 175000),
    ("Lenovo", "ThinkPad T14 Gen 3 Core i7 16GB", "laptops", 78000, 96000),
    ("Lenovo", "Legion 5 Pro RTX 3070 16GB 1TB", "laptops", 120000, 148000),
    ("Asus", "ZenBook 14 OLED Core i7 16GB 1TB", "laptops", 95000, 118000),
    # Audio & Accessories
    ("Apple", "AirPods Pro 2nd Gen USB-C MagSafe", "audio", 26000, 32000),
    ("Apple", "AirPods Max Space Gray with Smart Case", "audio", 62000, 75000),
    ("Sony", "WH-1000XM5 Wireless Noise Canceling Headphones", "audio", 38000, 48000),
    ("Sony", "WF-1000XM4 Noise Canceling Earbuds", "audio", 21000, 27000),
    ("JBL", "Boombox 3 Wi-Fi & Bluetooth Speaker", "audio", 46000, 55000),
    ("JBL", "Flip 6 Portable Waterproof Speaker", "audio", 14000, 18000),
    ("Anker", "Soundcore Motion+ 30W Hi-Res Bluetooth", "audio", 11000, 15000),
    ("Anker", "PowerCore 26800mAh Fast Charging Bank", "accessories", 6500, 9000),
    # Tablets & Cameras
    ("Apple", "iPad Pro 12.9 M2 128GB Wi-Fi Space Gray", "tablets", 130000, 165000),
    ("Apple", "iPad Air 5th Gen M1 64GB Starlight", "tablets", 72000, 88000),
    ("Samsung", "Galaxy Tab S9 128GB with S Pen", "tablets", 82000, 98000),
    ("Sony", "Alpha A7 IV Mirrorless Camera Body", "cameras", 220000, 270000),
    ("Canon", "EOS R6 Mark II Mirrorless with 24-105mm", "cameras", 245000, 310000),
    # Vehicles & Auto
    ("Toyota", "Vitz 2018 Compact Automatic Low Mileage", "vehicles", 1850000, 2200000),
    ("Toyota", "Yaris 2020 Sedan Excellent Condition", "vehicles", 2200000, 2600000),
    ("Toyota", "Corolla Executive 2021 Automatic", "vehicles", 3200000, 3700000),
    ("Toyota", "RAV4 Hybrid 2022 Full Option Bole", "vehicles", 5500000, 6400000),
    ("Hyundai", "Elantra 2019 Clean Title Addis", "vehicles", 2400000, 2900000),
    ("Suzuki", "Swift 2022 DualJet Fuel Efficient", "vehicles", 1750000, 2100000),
]


class Command(BaseCommand):
    help = "Generates realistic bulk data for load testing and search benchmarking."

    def add_arguments(self, parser):
        parser.add_argument(
            "--count",
            type=int,
            default=10000,
            help="Total number of products to generate (default: 10,000).",
        )
        parser.add_argument(
            "--shops",
            type=int,
            default=100,
            help="Number of shops to ensure/generate (default: 100).",
        )
        parser.add_argument(
            "--clean",
            action="store_true",
            help="Clean up previously generated perf data before generating.",
        )

    def handle(self, *args, **options):
        target_count = options["count"]
        shop_count = options["shops"]
        clean = options["clean"]

        self.stdout.write(
            f"Starting performance data generation (Target: {target_count:,} products)..."
        )

        if clean:
            self.stdout.write("Cleaning existing performance test data...")
            deleted_products, _ = Product.objects.filter(
                slug__contains="-perf-"
            ).delete()
            deleted_shops, _ = Shop.objects.filter(slug__contains="-perf-").delete()
            self.stdout.write(
                f"Removed {deleted_products} products and {deleted_shops} shops."
            )

        # 1. Ensure Categories exist
        category_map = {}
        for cat in CATEGORIES:
            obj, _ = Category.objects.get_or_create(
                slug=cat["slug"],
                defaults={
                    "name": cat["name"],
                    "display_order": cat["display_order"],
                },
            )
            category_map[cat["slug"]] = obj

        # 2. Ensure Shops exist
        self.stdout.write(f"Ensuring {shop_count} benchmark shops...")
        existing_shops = list(Shop.objects.filter(slug__contains="-perf-"))
        shops_to_create = shop_count - len(existing_shops)

        if shops_to_create > 0:
            # Ensure merchant user exists
            merchant_user, _ = User.objects.get_or_create(
                username="perf_merchant",
                defaults={
                    "email": "perf_merchant@tinsae.et",
                    "full_name": "Performance Benchmark Merchant",
                },
            )
            if not merchant_user.has_usable_password():
                merchant_user.set_password("perfpass123")
                merchant_user.save()

            new_shops = []
            for i in range(len(existing_shops) + 1, shop_count + 1):
                city = random.choice(CITIES)
                new_shops.append(
                    Shop(
                        name=f"{city} Electronics & Motors #{i}",
                        slug=f"shop-perf-{i}-{uuid4().hex[:6]}",
                        description=f"Authorized electronics and automotive dealer in {city}.",
                        phone=f"+251911{i:06d}"[:13],
                        city=city,
                        is_verified=(random.random() < 0.35),
                        owner=merchant_user,
                    )
                )
            created_shops = Shop.objects.bulk_create(new_shops)
            existing_shops.extend(created_shops)

        all_shops = existing_shops
        self.stdout.write(f"Available shops for assignment: {len(all_shops)}")

        # 3. Generate Products in Batches
        now = timezone.now()
        batch_size = 2000
        total_created = 0
        batch = []

        self.stdout.write(
            f"Generating {target_count:,} products in batches of {batch_size}..."
        )

        for i in range(1, target_count + 1):
            tpl = random.choice(PRODUCT_TEMPLATES)
            brand, base_name, cat_slug, min_p, max_p = tpl
            category = category_map.get(cat_slug) or list(category_map.values())[0]
            shop = random.choice(all_shops)

            # Price distribution
            price = Decimal(str(random.randint(min_p, max_p)))

            # Freshness distribution:
            # 60% Fresh (< 48h)
            # 25% Stale (3 - 25 days ago)
            # 15% Unconfirmed (last_confirmed_at = None)
            freshness_roll = random.random()
            if freshness_roll < 0.60:
                hours_ago = random.randint(1, 46)
                last_confirmed = now - timedelta(hours=hours_ago)
                conf_status = ConfirmationStatus.FRESH
            elif freshness_roll < 0.85:
                days_ago = random.randint(3, 25)
                last_confirmed = now - timedelta(days=days_ago)
                conf_status = ConfirmationStatus.STALE
            else:
                last_confirmed = None
                conf_status = ConfirmationStatus.UNCONFIRMED

            # Slight variations in names to test fuzzy matching & ranking
            variants = [
                "",
                " - New Edition",
                " (Original)",
                " - Brand New in Box",
                " Limited Stock",
            ]
            suffix = random.choice(variants)
            name = f"{base_name}{suffix}"
            slug = (
                f"{slugify(brand)}-{slugify(base_name)[:30]}-perf-{i}-{uuid4().hex[:6]}"
            )

            condition = random.choices(
                [
                    ProductCondition.NEW,
                    ProductCondition.USED,
                    ProductCondition.REFURBISHED,
                ],
                weights=[60, 30, 10],
            )[0]

            batch.append(
                Product(
                    category=category,
                    shop=shop,
                    brand=brand,
                    name=name,
                    slug=slug,
                    description=f"Genuine {brand} {name} available at {shop.name} in {shop.city}.",
                    price=price,
                    currency="ETB",
                    condition=condition,
                    last_confirmed_at=last_confirmed,
                    confirmation_status=conf_status,
                    status=ProductStatus.LIVE,
                    stock=random.randint(1, 15),
                    views=random.randint(0, 500),
                    inquiries=random.randint(0, 50),
                    is_available=True,
                )
            )

            if len(batch) >= batch_size:
                Product.objects.bulk_create(batch)
                total_created += len(batch)
                batch = []
                self.stdout.write(
                    f"Inserted {total_created:,} / {target_count:,} products..."
                )

        if batch:
            Product.objects.bulk_create(batch)
            total_created += len(batch)

        self.stdout.write(
            self.style.SUCCESS(
                f"Successfully generated {total_created:,} performance products across {len(all_shops)} shops!"
            )
        )
