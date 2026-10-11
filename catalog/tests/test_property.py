import io
from decimal import Decimal

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


class PropertySchemaTest(TestCase):
    """Sale or rent first; the deed for a sale, the advance for a rent."""

    def setUp(self):
        call_command("seed_catalog", stdout=io.StringIO())

    def test_categories_sit_under_property(self):
        children = Category.objects.filter(parent__slug="property")
        self.assertEqual(
            set(children.values_list("slug", flat=True)),
            {"apartments", "houses", "land", "commercial-property"},
        )

    def test_property_has_no_condition_and_asks_for_the_developer(self):
        schema = _schema("apartments")
        self.assertFalse(schema["has_condition"])
        self.assertEqual(schema["brand_label"], "Developer")
        self.assertFalse(schema["brand_required"])
        # Goods keep both defaults.
        phones = _schema("phones")
        self.assertTrue(phones["has_condition"])
        self.assertEqual(phones["brand_label"], "Brand")

    def test_the_deed_is_for_sales_and_the_advance_for_rents(self):
        schema = _schema("apartments")
        base = {
            "apartment_type": "apartment",
            "subcity": "bole",
            "area_name": "Atlas",
            "bedrooms": 2,
            "size_m2": 90,
        }
        sale = {**base, "listing_type": "sale", "advance_months": 3}
        self.assertIn("advance_months", validate_specs(schema, sale, condition="new"))
        rent = {**base, "listing_type": "rent", "title_deed": "ready"}
        self.assertIn("title_deed", validate_specs(schema, rent, condition="new"))
        ok = {**base, "listing_type": "rent", "advance_months": 3}
        self.assertEqual(validate_specs(schema, ok, condition="new"), {})

    def test_land_for_sale_needs_its_title_deed_status(self):
        schema = _schema("land")
        plot = {
            "listing_type": "sale",
            "subcity": "lemi_kura",
            "area_name": "Bole Arabsa",
            "plot_m2": 250,
            "land_use": "residential",
        }
        errors = validate_specs(schema, plot, condition="new")
        self.assertEqual(set(errors), {"title_deed"})


class AgencyMixin:
    """A signed-in agency that posts houses."""

    def setUp(self):
        call_command("seed_catalog", stdout=io.StringIO())
        owner = User.objects.create_user(username="agent", password="pass")
        self.shop = Shop.objects.create(name="Agency", owner=owner)
        self.client = APIClient()
        self.client.force_authenticate(owner)
        self.url = f"/api/v1/shops/{self.shop.slug}/products/"

    def _post(self, name, price, **specs):
        response = self.client.post(
            self.url,
            {
                "name": name,
                "price": price,
                "category": "houses",
                "condition": "new",
                "specs": {
                    "subcity": "bole",
                    "area_name": "Gerji",
                    "storeys": "g1",
                    "bedrooms": 5,
                    "plot_m2": 500,
                    **specs,
                },
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.data)


class PropertyListingTest(AgencyMixin, TestCase):
    def test_a_villa_priced_past_100_million_is_stored(self):
        self._post("Villa", "185000000.00", listing_type="sale")
        self.assertEqual(Product.objects.get().price, Decimal("185000000.00"))

    def test_buyers_filter_sale_from_rent_and_by_sub_city(self):
        self._post("For sale", "65000000.00", listing_type="sale")
        self._post("For rent", "85000.00", listing_type="rent")

        def titles(query):
            data = self.client.get(f"{PRODUCTS}?category=houses&{query}").data
            return [p["title"] for p in data["results"]]

        self.assertEqual(titles("spec_listing_type=rent"), ["For rent"])
        self.assertEqual(len(titles("spec_subcity=bole")), 2)
        self.assertEqual(titles("spec_subcity=yeka"), [])

    def test_condition_counts_and_filters_leave_property_out(self):
        self._post("House", "65000000.00", listing_type="sale")
        facets = self.client.get(
            "/api/v1/catalog/products/facets/?category=property"
        ).data
        self.assertEqual(facets["conditions"], [])
        data = self.client.get(f"{PRODUCTS}?condition=new").data
        self.assertEqual(data["results"], [])


class SaleOrRentTest(AgencyMixin, TestCase):
    """Renters and buyers each get their own side of the property pages."""

    def test_categories_say_which_are_for_sale_or_rent(self):
        rows = self.client.get("/api/v1/catalog/categories/").data["results"]
        flagged = {c["slug"] for c in rows if c["sale_or_rent"]}
        self.assertEqual(
            flagged,
            {"property", "apartments", "houses", "land", "commercial-property"},
        )

    def test_the_whole_group_filters_to_one_side(self):
        self._post("Villa", "65000000.00", listing_type="sale")
        self._post("House to rent", "85000.00", listing_type="rent")
        data = self.client.get(
            f"{PRODUCTS}?category=property&spec_listing_type=rent"
        ).data
        self.assertEqual([p["title"] for p in data["results"]], ["House to rent"])
        # Only what every property type shares: bedrooms are not on land.
        response = self.client.get(f"{PRODUCTS}?category=property&spec_bedrooms_min=2")
        self.assertEqual(response.status_code, 400)

    def test_counts_and_price_range_follow_the_side(self):
        self._post("Villa", "65000000.00", listing_type="sale")
        self._post("House to rent", "85000.00", listing_type="rent")
        facets = self.client.get(
            "/api/v1/catalog/products/facets/?category=property&spec_listing_type=rent"
        ).data
        self.assertEqual(facets["categories"]["houses"], 1)
        self.assertEqual(facets["price"], {"min": 85000.0, "max": 85000.0})
        sides = {
            v["value"]: v["count"] for v in facets["specs"]["listing_type"]["values"]
        }
        self.assertEqual(sides, {"sale": 1, "rent": 1})
