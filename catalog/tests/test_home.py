import io

from django.core.management import call_command
from django.test import TestCase
from rest_framework.test import APIClient

from shops.models import Shop
from users.models import User


class FurnitureAndAppliancesTest(TestCase):
    def setUp(self):
        call_command("seed_catalog", stdout=io.StringIO())
        owner = User.objects.create_user(username="homeshop", password="pass")
        self.shop = Shop.objects.create(name="Home Shop", owner=owner)
        self.client = APIClient()
        self.client.force_authenticate(owner)
        self.url = f"/api/v1/shops/{self.shop.slug}/products/"

    def _post(self, category, specs, *, brand="", name="Item", status="live"):
        return self.client.post(
            self.url,
            {
                "brand": brand,
                "name": name,
                "price": "10000.00",
                "category": category,
                "condition": "new",
                "status": status,
                "specs": specs,
            },
            format="json",
        )

    def test_unbranded_furniture_is_fine_and_its_title_is_just_the_name(self):
        specs = {"furniture_type": "sofa", "material": "Leather", "seats": 3}
        response = self._post("furniture", specs, name="3-Seater Leather Sofa")
        self.assertEqual(response.status_code, 201, response.data)
        listing = self.client.get(
            f"/api/v1/catalog/products/{response.data['slug']}/"
        ).data
        self.assertEqual(listing["title"], "3-Seater Leather Sofa")

    def test_an_appliance_still_needs_its_brand_except_as_a_draft(self):
        specs = {
            "appliance_type": "refrigerator",
            "warranty": "Brand · 1 year",
        }
        response = self._post("appliances", specs, name="345L Fridge")
        self.assertEqual(response.status_code, 400)
        self.assertIn("brand", response.data)
        response = self._post("appliances", specs, name="345L Fridge", status="draft")
        self.assertEqual(response.status_code, 201, response.data)

    def test_type_specific_fields_follow_the_type(self):
        # A mattress has a thickness; a pillow has no bed size.
        response = self._post(
            "beds-mattresses",
            {"bed_item_type": "pillow", "bed_size": "queen"},
            name="Pillow",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("bed_size", response.data["specs"])
        response = self._post(
            "beds-mattresses",
            {"bed_item_type": "mattress", "bed_size": "queen", "thickness_cm": 25},
            name="Mattress",
        )
        self.assertEqual(response.status_code, 201, response.data)

    def test_the_schema_carries_form_examples_and_the_brand_rule(self):
        schema = self.client.get("/api/v1/catalog/categories/furniture/schema/").data
        self.assertFalse(schema["brand_required"])
        self.assertEqual(schema["listing_examples"]["name"], "3-Seater Leather Sofa")
        cars = self.client.get("/api/v1/catalog/categories/cars/schema/").data
        self.assertTrue(cars["brand_required"])
