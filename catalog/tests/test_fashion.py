import io

from django.core.management import call_command
from django.test import TestCase
from rest_framework.test import APIClient

from catalog.models import Category, Product
from catalog.specs import category_schema, validate_specs
from shops.models import Shop
from users.models import User

PRODUCTS = "/api/v1/catalog/products/"


def _schema(slug):
    return category_schema(Category.objects.get(slug=slug))


class FashionSchemaTest(TestCase):
    """Sizes and colours are lists; gold needs its karat and weight."""

    def setUp(self):
        call_command("seed_catalog", stdout=io.StringIO())

    def test_categories_sit_under_fashion(self):
        children = Category.objects.filter(parent__slug="fashion")
        self.assertEqual(
            set(children.values_list("slug", flat=True)),
            {
                "mens-clothing",
                "womens-clothing",
                "kids-clothing",
                "shoes",
                "bags",
                "watches-jewelry",
            },
        )

    def test_clothing_needs_sizes_as_a_list(self):
        schema = _schema("mens-clothing")
        fields = {f["key"]: f for f in schema["fields"]}
        self.assertTrue(fields["sizes"]["multiple"])
        self.assertTrue(fields["color"]["multiple"])
        self.assertFalse(schema["brand_required"])

        errors = validate_specs(schema, {"clothing_type": "shirt"}, condition="new")
        self.assertIn("sizes", errors)
        ok = {"clothing_type": "shirt", "sizes": ["M", "L"], "color": ["White"]}
        self.assertEqual(validate_specs(schema, ok, condition="new"), {})

    def test_dresses_are_offered_to_women_and_kids_only(self):
        def types(slug):
            fields = {f["key"]: f for f in _schema(slug)["fields"]}
            return {o["value"] for o in fields["clothing_type"]["options"]}

        self.assertNotIn("dress", types("mens-clothing"))
        self.assertIn("dress", types("womens-clothing"))
        self.assertIn("dress", types("kids-clothing"))

    def test_kids_sizes_go_by_age(self):
        fields = {f["key"]: f for f in _schema("kids-clothing")["fields"]}
        self.assertIn("kids_size", fields)
        self.assertNotIn("sizes", fields)

    def test_gold_needs_karat_and_weight_silver_does_not(self):
        schema = _schema("watches-jewelry")
        gold = {"jewelry_type": "ring", "metal": "gold"}
        errors = validate_specs(schema, gold, condition="new")
        self.assertEqual(set(errors), {"karat", "weight_g"})
        silver = {"jewelry_type": "ring", "metal": "silver"}
        self.assertEqual(validate_specs(schema, silver, condition="new"), {})

    def test_movement_applies_to_watches_only(self):
        schema = _schema("watches-jewelry")
        ring = {"jewelry_type": "ring", "metal": "silver", "movement": "quartz"}
        self.assertIn("movement", validate_specs(schema, ring, condition="new"))


class FashionFilterTest(TestCase):
    def setUp(self):
        call_command("seed_catalog", stdout=io.StringIO())
        owner = User.objects.create_user(username="fashion", password="pass")
        self.shop = Shop.objects.create(name="Fashion Shop", owner=owner)
        self.client = APIClient()
        self.client.force_authenticate(owner)
        self.url = f"/api/v1/shops/{self.shop.slug}/products/"

    def _post(self, name, sizes):
        response = self.client.post(
            self.url,
            {
                "name": name,
                "price": "1500.00",
                "category": "mens-clothing",
                "condition": "new",
                "specs": {"clothing_type": "tshirt", "sizes": sizes},
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.data)

    def test_a_size_finds_every_listing_that_carries_it(self):
        self._post("Small run", ["s", "m"])
        self._post("Large run", ["L", "XL"])
        data = self.client.get(f"{PRODUCTS}?category=mens-clothing&spec_sizes=M").data
        self.assertEqual([p["title"] for p in data["results"]], ["Small run"])
        # Typed sizes are tidied to the suggestion's label: "s" is stored as "S".
        stored = Product.objects.get(name="Small run").specs["sizes"]
        self.assertEqual(stored, ["S", "M"])

    def test_sizes_are_listed_in_size_order_not_by_count(self):
        self._post("Big run", ["XL", "L", "M"])
        self._post("Another", ["L", "32"])
        self._post("Small", ["S", "L", "30"])
        facets = self.client.get(
            "/api/v1/catalog/products/facets/?category=mens-clothing"
        ).data
        values = [v["value"] for v in facets["specs"]["sizes"]["values"]]
        self.assertEqual(values, ["S", "M", "L", "XL", "30", "32"])
