from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from catalog.models import Category, Product
from shops.models import Shop
from users.models import User


class ProductApiTest(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username="test_merchant", password="pass")
        self.shop = Shop.objects.create(
            name="Test Shop", slug="test-shop", owner=self.owner
        )
        self.cat = Category.objects.create(name="Phones", slug="phones")
        self.product = Product.objects.create(
            category=self.cat,
            shop=self.shop,
            brand="Samsung",
            name="Galaxy A15",
            slug="galaxy-a15",
            price="12500.00",
            specs={"ram": "4GB", "storage": "128GB"},
            last_confirmed_at=timezone.now(),
        )

    def test_product_list_is_paginated_and_contains_product(self):
        response = APIClient().get("/api/v1/catalog/products/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(response.data["results"][0]["slug"], "galaxy-a15")

    def test_filter_by_category_slug(self):
        acc = Category.objects.create(name="Accessories", slug="accessories")
        Product.objects.create(
            category=acc,
            shop=self.shop,
            brand="Anker",
            name="PowerPort",
            slug="powerport",
            price="450.00",
        )

        response = APIClient().get("/api/v1/catalog/products/?category=phones")
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(response.data["results"][0]["title"], "Samsung Galaxy A15")

    def test_product_detail_by_slug(self):
        response = APIClient().get("/api/v1/catalog/products/galaxy-a15/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["specs"], {"ram": "4GB", "storage": "128GB"})

    def test_search_keyword_match(self):
        response = APIClient().get("/api/v1/catalog/products/?q=gala")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["results"][0]["title"], "Samsung Galaxy A15")

    def test_max_price_filter(self):
        response = APIClient().get("/api/v1/catalog/products/?max_price=20000.00")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(float(response.data["results"][0]["price"]), 12500.00)

    def test_min_price_filter(self):
        response = APIClient().get("/api/v1/catalog/products/?min_price=10000.00")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(float(response.data["results"][0]["price"]), 12500.00)

    def test_vefified_only_filter(self):
        owner1 = User.objects.create_user(username="test_merchant1", password="pass")

        shop1 = Shop.objects.create(
            name="Test Shop1", slug="test-shop1", owner=owner1, is_verified=True
        )
        cat = Category.objects.create(name="Phones1", slug="phones1")
        Product.objects.create(
            category=cat,
            shop=shop1,
            brand="Samsung",
            name="Galaxy A55",
            slug="galaxy-a55",
            price="32500.00",
            specs={"ram": "12GB", "storage": "128GB"},
        )

        respose = APIClient().get("/api/v1/catalog/products/?verified_only=ok")
        self.assertEqual(respose.status_code, 200)
        self.assertEqual(respose.data["results"][0]["title"], "Samsung Galaxy A55")

    def test_freshness_filter(self):
        owner1 = User.objects.create_user(username="test_merchant1", password="pass")

        shop1 = Shop.objects.create(
            name="Test Shop1", slug="test-shop1", owner=owner1, is_verified=True
        )
        cat = Category.objects.create(name="Phones1", slug="phones1")
        Product.objects.create(
            category=cat,
            shop=shop1,
            brand="Samsung",
            name="Galaxy A55",
            slug="galaxy-a55",
            price="45500.00",
            specs={"ram": "12GB", "storage": "128GB"},
            last_confirmed_at=timezone.now(),
        )

        response = APIClient().get("/api/v1/catalog/products/?freshness=ok")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data["results"]), 2)

    def test_combined_filtering(self):
        owner1 = User.objects.create_user(username="test_merchant1", password="pass")

        shop1 = Shop.objects.create(
            name="Test Shop1", slug="test-shop1", owner=owner1, is_verified=True
        )
        cat = Category.objects.create(name="Phones1", slug="phones1")
        Product.objects.create(
            category=cat,
            shop=shop1,
            brand="Samsung",
            name="Galaxy A55",
            slug="galaxy-a55",
            price="45500.00",
            specs={"ram": "12GB", "storage": "128GB"},
            last_confirmed_at=timezone.now(),
        )
        Product.objects.create(
            category=cat,
            shop=shop1,
            brand="Samsung",
            name="Galaxy A35",
            slug="galaxy-a35",
            price="32500.00",
            specs={"ram": "6GB", "storage": "128GB"},
            last_confirmed_at=timezone.now(),
        )

        response = APIClient().get(
            "/api/v1/catalog/products/?q=gala&min_price=20000&max_price=35000&freshness=fresh&verified_only=ok&category=phones1"
        )
        self.assertEqual(response.status_code, 200)

        self.assertEqual(response.data["results"][0]["title"], "Samsung Galaxy A35")

    def _create_cheaper_product(self):
        return Product.objects.create(
            category=self.cat,
            shop=self.shop,
            brand="Samsung",
            name="Galaxy J2",
            slug="galaxy-j2",
            price="2000.00",
            specs={},
        )

    def test_ordering_by_price_ascending(self):
        self._create_cheaper_product()
        response = APIClient().get("/api/v1/catalog/products/?ordering=price")
        self.assertEqual(response.status_code, 200)
        titles = [item["title"] for item in response.data["results"]]
        self.assertEqual(titles, ["Samsung Galaxy J2", "Samsung Galaxy A15"])

    def test_ordering_by_price_descending(self):
        self._create_cheaper_product()
        response = APIClient().get("/api/v1/catalog/products/?ordering=-price")
        self.assertEqual(response.status_code, 200)
        titles = [item["title"] for item in response.data["results"]]
        self.assertEqual(titles, ["Samsung Galaxy A15", "Samsung Galaxy J2"])

    def test_ordering_by_invalid_field_rejected(self):
        response = APIClient().get(
            "/api/v1/catalog/products/?ordering=__secret_field__"
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("ordering", response.data)
