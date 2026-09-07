from decimal import Decimal

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from catalog.models import Category, ConfirmationStatus, Product
from shops.models import Shop
from users.models import User


class ProductSuggestionsTest(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            username="tech_merchant", email="shop@tinsae.et", password="password"
        )
        self.shop_addis = Shop.objects.create(
            name="Tech Shop Addis",
            slug="tech-shop-addis",
            owner=self.user,
            city="Addis Ababa",
            is_verified=True,
        )
        self.shop_hawassa = Shop.objects.create(
            name="Tech Shop Hawassa",
            slug="tech-shop-hawassa",
            owner=self.user,
            city="Hawassa",
            is_verified=False,
        )

        self.phones_cat = Category.objects.create(
            name="Phones", slug="phones", display_order=0
        )
        self.laptops_cat = Category.objects.create(
            name="Laptops", slug="laptops", display_order=1
        )

        # Target Product: iPhone 15 at 100,000 ETB in Addis Ababa
        self.target = Product.objects.create(
            name="iPhone 15",
            slug="iphone-15",
            brand="Apple",
            price=Decimal("100000.00"),
            shop=self.shop_addis,
            category=self.phones_cat,
            is_available=True,
        )

        # Candidate 1: Tier 1 (Apple, 90,000 ETB - within ±30%)
        self.p_tier1 = Product.objects.create(
            name="iPhone 14",
            slug="iphone-14",
            brand="Apple",
            price=Decimal("90000.00"),
            shop=self.shop_addis,
            category=self.phones_cat,
            is_available=True,
        )

        # Candidate 2: Tier 2 (Samsung, 95,000 ETB - competitor in ±30%)
        self.p_tier2 = Product.objects.create(
            name="Galaxy S24",
            slug="galaxy-s24",
            brand="Samsung",
            price=Decimal("95000.00"),
            shop=self.shop_addis,
            category=self.phones_cat,
            is_available=True,
        )

        # Candidate 3: Tier 3 (Apple, 170,000 ETB - same brand, outside ±30%)
        self.p_tier3 = Product.objects.create(
            name="iPhone 15 Pro Max",
            slug="iphone-15-pro-max",
            brand="Apple",
            price=Decimal("170000.00"),
            shop=self.shop_addis,
            category=self.phones_cat,
            is_available=True,
        )

        # Candidate 4: Tier 4 (Tecno, 15,000 ETB - other brand, outside ±30%)
        self.p_tier4 = Product.objects.create(
            name="Spark 20",
            slug="spark-20",
            brand="Tecno",
            price=Decimal("15000.00"),
            shop=self.shop_addis,
            category=self.phones_cat,
            is_available=True,
        )

        # Candidate 5: Different Category (Laptops) - MUST NEVER appear
        self.laptop = Product.objects.create(
            name="MacBook Air",
            slug="macbook-air",
            brand="Apple",
            price=Decimal("95000.00"),
            shop=self.shop_addis,
            category=self.laptops_cat,
            is_available=True,
        )

        # Candidate 6: Unavailable in same category - MUST NEVER appear
        self.sold_iphone = Product.objects.create(
            name="iPhone 13 Sold",
            slug="iphone-13-sold",
            brand="Apple",
            price=Decimal("85000.00"),
            shop=self.shop_addis,
            category=self.phones_cat,
            is_available=False,
        )

    def test_suggestions_ranking_and_category_isolation(self):
        url = f"/api/v1/catalog/products/{self.target.slug}/suggestions/"
        res = self.client.get(url)
        self.assertEqual(res.status_code, 200)

        results = res.data["results"]
        result_ids = [p["id"] for p in results]

        # Target, other category, and unavailable products must NOT appear
        self.assertNotIn(self.target.id, result_ids)
        self.assertNotIn(self.laptop.id, result_ids)
        self.assertNotIn(self.sold_iphone.id, result_ids)

        # Strictly ordered Tier 1 -> Tier 2 -> Tier 3 -> Tier 4
        self.assertEqual(
            result_ids,
            [self.p_tier1.id, self.p_tier2.id, self.p_tier3.id, self.p_tier4.id],
        )

    def test_suggestions_work_when_target_product_is_sold_out(self):
        """Recommending available alternatives for an out-of-stock item is critical."""
        self.target.is_available = False
        self.target.save(update_fields=["is_available"])

        url = f"/api/v1/catalog/products/{self.target.slug}/suggestions/"
        res = self.client.get(url)
        self.assertEqual(res.status_code, 200)
        self.assertGreater(len(res.data["results"]), 0)
        # All returned suggestions must be in stock
        for item in res.data["results"]:
            self.assertNotEqual(item["id"], self.target.id)

    def test_multi_signal_tie_breaking_within_same_tier(self):
        """Inside the same tier, fresh verified items in the same city rank highest."""
        now = timezone.now()

        # Two Tier 1 candidates (Apple, in price window):
        # Item A: Fresh confirmed today, verified shop in Addis Ababa
        item_a = Product.objects.create(
            name="iPhone 14 Plus",
            slug="iphone-14-plus",
            brand="Apple",
            price=Decimal("92000.00"),
            shop=self.shop_addis,
            category=self.phones_cat,
            is_available=True,
            confirmation_status=ConfirmationStatus.FRESH,
            last_confirmed_at=now,
        )

        # Item B: Unconfirmed, unverified shop in Hawassa
        item_b = Product.objects.create(
            name="iPhone 14 Pro",
            slug="iphone-14-pro",
            brand="Apple",
            price=Decimal("94000.00"),
            shop=self.shop_hawassa,
            category=self.phones_cat,
            is_available=True,
            confirmation_status=ConfirmationStatus.UNCONFIRMED,
            last_confirmed_at=None,
        )

        url = f"/api/v1/catalog/products/{self.target.slug}/suggestions/"
        res = self.client.get(url)
        self.assertEqual(res.status_code, 200)

        result_ids = [p["id"] for p in res.data["results"]]
        # Item A must rank before Item B
        self.assertLess(result_ids.index(item_a.id), result_ids.index(item_b.id))

    def test_pagination_defaults_and_limits(self):
        url = f"/api/v1/catalog/products/{self.target.slug}/suggestions/?page_size=2"
        res = self.client.get(url)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(len(res.data["results"]), 2)
        self.assertIsNotNone(res.data["next"])
        self.assertIsNone(res.data["previous"])

        # Fetch second page
        res_page2 = self.client.get(res.data["next"])
        self.assertEqual(res_page2.status_code, 200)
        self.assertEqual(len(res_page2.data["results"]), 2)
        self.assertIsNotNone(res_page2.data["previous"])

    def test_page_out_of_range_returns_404(self):
        url = f"/api/v1/catalog/products/{self.target.slug}/suggestions/?page=999"
        res = self.client.get(url)
        self.assertEqual(res.status_code, 404)

    def test_unknown_slug_returns_404(self):
        url = "/api/v1/catalog/products/non-existent-slug/suggestions/"
        res = self.client.get(url)
        self.assertEqual(res.status_code, 404)

    def test_empty_brand_and_price_does_not_crash(self):
        no_brand_prod = Product.objects.create(
            name="Unbranded Cable",
            slug="unbranded-cable",
            brand="",
            price=Decimal("0.00"),
            shop=self.shop_addis,
            category=self.phones_cat,
            is_available=True,
        )
        url = f"/api/v1/catalog/products/{no_brand_prod.slug}/suggestions/"
        res = self.client.get(url)
        self.assertEqual(res.status_code, 200)
