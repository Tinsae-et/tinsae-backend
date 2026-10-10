import io

from django.core.management import call_command
from django.test import TestCase
from rest_framework.test import APIClient

from shops.models import Shop
from users.models import User

PRODUCTS = "/api/v1/catalog/products/"
FACETS = "/api/v1/catalog/products/facets/"


class MultiValueSpecTest(TestCase):
    """A spec holding several values: a watch's sensors, later a shirt's sizes."""

    def setUp(self):
        call_command("seed_catalog", stdout=io.StringIO())
        owner = User.objects.create_user(username="watches", password="pass")
        self.shop = Shop.objects.create(name="Watch Shop", owner=owner)
        self.client = APIClient()
        self.client.force_authenticate(owner)
        self.url = f"/api/v1/shops/{self.shop.slug}/products/"

    def _post(self, sensors, name="Watch"):
        return self.client.post(
            self.url,
            {
                "brand": "Apple",
                "name": name,
                "price": "50000.00",
                "category": "wearables",
                "condition": "new",
                "specs": {
                    "wearable_type": "smartwatch",
                    "warranty": "Brand · 1 year",
                    "sensors": sensors,
                },
            },
            format="json",
        )

    def test_a_list_is_stored_as_given(self):
        response = self._post(["heart_rate", "spo2", "ecg"])
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(
            response.data["specs"]["sensors"], ["heart_rate", "spo2", "ecg"]
        )

    def test_every_value_is_checked(self):
        for sensors, why in (
            ("ecg", "a single value is not a list"),
            (["ecg", "ecg"], "a value twice"),
            (["ecg", "x-ray"], "an unknown value"),
        ):
            with self.subTest(why):
                response = self._post(sensors)
                self.assertEqual(response.status_code, 400)
                self.assertIn("sensors", response.data["specs"])

    def test_an_empty_list_counts_as_not_filled_in(self):
        # Sensors are recommended, not required: an empty list is fine.
        response = self._post([])
        self.assertEqual(response.status_code, 201, response.data)

    def test_a_filter_matches_any_value_in_the_list(self):
        self._post(["heart_rate", "ecg"], name="Medical")
        self._post(["heart_rate", "spo2"], name="Fitness")
        self._post(["heart_rate"], name="Basic")

        def titles(query):
            data = self.client.get(f"{PRODUCTS}?category=wearables&{query}").data
            return sorted(p["title"] for p in data["results"])

        self.assertEqual(titles("spec_sensors=ecg"), ["Apple Medical"])
        self.assertEqual(
            titles("spec_sensors=ecg,spo2"), ["Apple Fitness", "Apple Medical"]
        )
        self.assertEqual(len(titles("spec_sensors=heart_rate")), 3)

    def test_facets_count_each_value_separately(self):
        self._post(["heart_rate", "ecg"], name="Medical")
        self._post(["heart_rate", "spo2"], name="Fitness")
        facets = self.client.get(f"{FACETS}?category=wearables").data
        counts = {v["value"]: v["count"] for v in facets["specs"]["sensors"]["values"]}
        self.assertEqual(counts, {"heart_rate": 2, "ecg": 1, "spo2": 1})
