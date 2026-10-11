import io

from django.core.management import call_command
from django.test import TestCase

from catalog.admin import ProductAdminForm
from catalog.models import Category
from shops.models import Shop
from users.models import User


class ProductAdminSpecsTest(TestCase):
    """Admin edits follow the same spec rules as the shop dashboard."""

    def setUp(self):
        call_command("seed_catalog", stdout=io.StringIO())
        owner = User.objects.create_user(username="admin-specs", password="pass")
        self.shop = Shop.objects.create(name="Admin Shop", owner=owner)
        self.cars = Category.objects.get(slug="cars")

    def _form(self, specs, **overrides):
        data = {
            "shop": self.shop.pk,
            "category": self.cars.pk,
            "brand": "Toyota",
            "name": "Vitz",
            "slug": "admin-vitz",
            "price": "1500000.00",
            "currency": "ETB",
            "condition": "new",
            "confirmation_status": "fresh",
            "status": "live",
            "stock": 1,
            "views": 0,
            "inquiries": 0,
            "is_available": True,
            "specs": specs,
            **overrides,
        }
        return ProductAdminForm(data=data)

    def test_invalid_specs_are_rejected(self):
        form = self._form('{"body_type": "spaceship", "wings": 2}')
        self.assertFalse(form.is_valid())
        self.assertIn("specs", form.errors)

    def test_valid_specs_are_tidied_and_accepted(self):
        form = self._form(
            '{"body_type": "hatchback", "year": 2015, "fuel": "petrol",'
            ' "transmission": "automatic", "color": "white"}'
        )
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["specs"]["color"], "White")

    def test_list_specs_are_tidied_and_accepted(self):
        shirts = Category.objects.get(slug="mens-clothing")
        form = self._form(
            '{"clothing_type": "shirt", "sizes": ["m", " l "], "color": ["navy"]}',
            category=shirts.pk,
            brand="",
            slug="admin-shirt",
        )
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["specs"]["sizes"], ["M", "L"])
        self.assertEqual(form.cleaned_data["specs"]["color"], ["Navy"])

    def test_a_single_value_where_a_list_belongs_is_rejected(self):
        shirts = Category.objects.get(slug="mens-clothing")
        form = self._form(
            '{"clothing_type": "shirt", "sizes": "M"}',
            category=shirts.pk,
            slug="admin-shirt",
        )
        self.assertFalse(form.is_valid())
        self.assertIn("sizes", str(form.errors["specs"]))
