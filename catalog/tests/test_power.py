import io

from django.core.management import call_command
from django.test import TestCase
from rest_framework.test import APIClient

from shops.models import Shop
from users.models import User


class PowerAndSolarTest(TestCase):
    def setUp(self):
        call_command("seed_catalog", stdout=io.StringIO())
        owner = User.objects.create_user(username="powershop", password="pass")
        self.shop = Shop.objects.create(name="Power Shop", owner=owner)
        self.client = APIClient()
        self.client.force_authenticate(owner)
        self.url = f"/api/v1/shops/{self.shop.slug}/products/"

    def _post(self, category, specs, *, condition="new", name="Item"):
        return self.client.post(
            self.url,
            {
                "brand": "Felicity",
                "name": name,
                "price": "50000.00",
                "category": category,
                "condition": condition,
                "specs": {**specs, "warranty": "Brand · 1 year"},
            },
            format="json",
        )

    def test_a_used_generator_needs_its_running_hours(self):
        specs = {"power_kva": 5, "fuel": "diesel", "phase": "single"}
        response = self._post("generators", specs, condition="used", name="Gen")
        self.assertEqual(response.status_code, 400)
        self.assertIn("operating_hours", response.data["specs"])
        response = self._post(
            "generators",
            {**specs, "operating_hours": 1200},
            condition="used",
            name="Gen",
        )
        self.assertEqual(response.status_code, 201, response.data)

    def test_a_generator_runs_on_petrol_or_diesel_only(self):
        response = self._post(
            "generators", {"power_kva": 5, "fuel": "electric", "phase": "single"}
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("fuel", response.data["specs"])

    def test_inverter_and_battery_specs_follow_the_type(self):
        # A battery has no output wave; an inverter needs its power.
        response = self._post(
            "inverters-batteries",
            {"backup_type": "battery", "capacity_ah": 200, "output_wave": "pure_sine"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("output_wave", response.data["specs"])
        response = self._post("inverters-batteries", {"backup_type": "inverter"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("power_w", response.data["specs"])
        response = self._post(
            "inverters-batteries",
            {"backup_type": "inverter", "power_w": 5000, "solar_input": "yes"},
            name="Hybrid",
        )
        self.assertEqual(response.status_code, 201, response.data)
