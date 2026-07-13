from django.test import TestCase
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
        self.assertEqual(response.data["results"][0]["brand"], "Samsung")

    def test_product_detail_by_slug(self):
        response = APIClient().get("/api/v1/catalog/products/galaxy-a15/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["specs"], {"ram": "4GB", "storage": "128GB"})
