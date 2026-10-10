import io

from django.core.management import call_command
from django.test import TestCase
from rest_framework.test import APIClient

from catalog.models import Category
from shops.models import Shop
from users.models import User


class ElectronicsGroupsTest(TestCase):
    """Electronics grouped like vehicles: Phones & Tablets, Computers, TV & Audio."""

    def setUp(self):
        call_command("seed_catalog", stdout=io.StringIO())
        owner = User.objects.create_user(username="tvshop", password="pass")
        self.shop = Shop.objects.create(name="TV Shop", owner=owner)
        self.client = APIClient()
        self.client.force_authenticate(owner)
        self.url = f"/api/v1/shops/{self.shop.slug}/products/"

    def test_existing_slugs_moved_under_their_groups(self):
        parents = dict(
            Category.objects.filter(parent__isnull=False).values_list(
                "slug", "parent__slug"
            )
        )
        self.assertEqual(parents["phones"], "phones-tablets")
        self.assertEqual(parents["wearables"], "phones-tablets")
        self.assertEqual(parents["laptops"], "computers")
        self.assertEqual(parents["monitors"], "computers")
        self.assertEqual(parents["tvs"], "tv-audio")
        self.assertEqual(parents["audio"], "tv-audio")
        # Cameras and Accessories stay top-level.
        self.assertNotIn("cameras", parents)

    def test_the_category_list_returns_every_category_in_one_page(self):
        response = self.client.get("/api/v1/catalog/categories/")
        self.assertEqual(response.status_code, 200)
        # More than the default page of 20, all of them in `results`.
        self.assertGreater(response.data["count"], 20)
        self.assertEqual(len(response.data["results"]), response.data["count"])
        self.assertIsNone(response.data["next"])

    def test_a_tv_platform_only_applies_to_a_smart_tv(self):
        base = {
            "brand": "Skyworth",
            "price": "12500.00",
            "category": "tvs",
            "condition": "new",
        }
        specs = {
            "screen_size": 32,
            "display_resolution": "hd",
            "smart_tv": "no",
            "warranty": "Shop · 1 year",
        }
        response = self.client.post(
            self.url, {**base, "name": "32 HD", "specs": specs}, format="json"
        )
        self.assertEqual(response.status_code, 201, response.data)

        response = self.client.post(
            self.url,
            {**base, "name": "32 HD Tizen", "specs": {**specs, "tv_os": "Tizen"}},
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("tv_os", response.data["specs"])
