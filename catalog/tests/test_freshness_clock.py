"""End-to-end verification of the unified 7d due / 14d hidden freshness clock."""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from bot.selectors import due_products_for_shop
from catalog.freshness import Freshness
from catalog.models import Category, Product, ProductStatus
from shops.models import Shop

User = get_user_model()


class UnifiedFreshnessClockEndToEndTests(TestCase):
    """Proves the single clock across models, buyer visibility, and digest eligibility:

    - Product confirmed 6d ago  -> FRESH and buyer-visible, NOT due for digest
    - Product confirmed 8d ago  -> ALMOST_EXPIRED, still buyer-visible, DUE for digest
    - Product confirmed 15d ago -> EXPIRED and NOT buyer-visible
    """

    def setUp(self):
        self.user = User.objects.create_user(username="clock_user", password="pw")
        self.shop = Shop.objects.create(
            name="Clock Shop",
            owner=self.user,
            telegram_chat_id="123456",
        )
        self.category = Category.objects.create(name="Devices", slug="devices")
        self.now = timezone.now()

        # 1. Confirmed 6 days ago (well inside 7d interval)
        self.p_6d = Product.objects.create(
            shop=self.shop,
            category=self.category,
            brand="BrandA",
            name="Device 6d",
            price="100.00",
            is_available=True,
            status=ProductStatus.LIVE,
            last_confirmed_at=self.now - timedelta(days=6),
        )

        # 2. Confirmed 8 days ago (past 7d, inside 14d hidden window)
        self.p_8d = Product.objects.create(
            shop=self.shop,
            category=self.category,
            brand="BrandB",
            name="Device 8d",
            price="200.00",
            is_available=True,
            status=ProductStatus.LIVE,
            last_confirmed_at=self.now - timedelta(days=8),
        )

        # 3. Confirmed 15 days ago (past 14d hidden window)
        self.p_15d = Product.objects.create(
            shop=self.shop,
            category=self.category,
            brand="BrandC",
            name="Device 15d",
            price="300.00",
            is_available=True,
            status=ProductStatus.LIVE,
            last_confirmed_at=self.now - timedelta(days=15),
        )

    def test_product_confirmed_6d_ago_is_fresh_and_buyer_visible(self):
        # Freshness property and fresh queryset
        self.assertEqual(self.p_6d.freshness, Freshness.FRESH)
        self.assertIn(self.p_6d, Product.objects.fresh(now=self.now))

        # Buyer visible
        buyer_visible = list(Product.objects.buyer_visible(now=self.now))
        self.assertIn(self.p_6d, buyer_visible)

        # Not due for digest (confirmed within 7 days)
        cutoff = self.now - timedelta(days=7)
        due = due_products_for_shop(self.shop, cutoff)
        self.assertNotIn(self.p_6d, due)

    def test_product_confirmed_8d_ago_is_almost_expired_buyer_visible_and_due(self):
        # Freshness property and fresh queryset
        self.assertEqual(self.p_8d.freshness, Freshness.ALMOST_EXPIRED)
        self.assertNotIn(self.p_8d, Product.objects.fresh(now=self.now))

        # Still buyer visible
        buyer_visible = list(Product.objects.buyer_visible(now=self.now))
        self.assertIn(self.p_8d, buyer_visible)

        # Due for digest
        cutoff = self.now - timedelta(days=7)
        due = due_products_for_shop(self.shop, cutoff)
        self.assertIn(self.p_8d, due)

    def test_product_confirmed_15d_ago_is_expired_and_not_buyer_visible(self):
        # Freshness property and fresh queryset
        self.assertEqual(self.p_15d.freshness, Freshness.EXPIRED)
        self.assertNotIn(self.p_15d, Product.objects.fresh(now=self.now))

        # NOT buyer visible (hidden after 14 days)
        buyer_visible = list(Product.objects.buyer_visible(now=self.now))
        self.assertNotIn(self.p_15d, buyer_visible)
