from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from catalog.models import Category, Product
from catalog.taxonomy import ATTRIBUTES, PRODUCT_TYPES
from catalog.taxonomy.sync import sync_taxonomy
from shops.models import Shop
from users.models import User

URL = "/api/v1/catalog/products/"
FACETS = "/api/v1/catalog/products/facets/"


class SpecFilterTest(TestCase):
    def setUp(self):
        vehicles = Category.objects.create(name="Vehicles & Auto", slug="vehicles")
        self.cars = Category.objects.create(name="Cars", slug="cars", parent=vehicles)
        self.bikes = Category.objects.create(
            name="Motorcycles", slug="motorcycles", parent=vehicles
        )
        sync_taxonomy(ATTRIBUTES, PRODUCT_TYPES)
        owner = User.objects.create_user(username="dealer", password="pass")
        self.shop = Shop.objects.create(name="Dealer", owner=owner)
        self.client = APIClient()
        self._car("Vitz", "Toyota", fuel="petrol", year=2014, color="White")
        self._car("Hilux", "Toyota", fuel="diesel", year=2019, color="Silver")
        self._car(
            "Tucson",
            "Hyundai",
            fuel="petrol",
            year=2021,
            color="white",
            condition="used",
        )
        Product.objects.create(
            shop=self.shop,
            category=self.bikes,
            brand="Bajaj",
            name="Boxer",
            price="140000.00",
            specs={"fuel": "petrol", "year": 2022},
            last_confirmed_at=timezone.now(),
        )

    def _car(self, name, brand, *, condition="new", **specs):
        return Product.objects.create(
            shop=self.shop,
            category=self.cars,
            brand=brand,
            name=name,
            price="2000000.00",
            condition=condition,
            specs=specs,
            last_confirmed_at=timezone.now(),
        )

    def _names(self, query):
        response = self.client.get(f"{URL}?{query}")
        self.assertEqual(response.status_code, 200, response.data)
        return sorted(p["title"] for p in response.data["results"])

    def test_an_option_filter_and_several_options_are_ored(self):
        self.assertEqual(
            self._names("category=cars&spec_fuel=diesel"), ["Toyota Hilux"]
        )
        self.assertEqual(
            self._names("category=cars&spec_fuel=diesel,petrol"),
            ["Hyundai Tucson", "Toyota Hilux", "Toyota Vitz"],
        )

    def test_a_number_range_with_either_end_open(self):
        self.assertEqual(
            self._names("category=cars&spec_year_min=2015"),
            ["Hyundai Tucson", "Toyota Hilux"],
        )
        self.assertEqual(
            self._names("category=cars&spec_year_min=2015&spec_year_max=2020"),
            ["Toyota Hilux"],
        )

    def test_text_matches_ignoring_case(self):
        self.assertEqual(
            self._names("category=cars&spec_color=WHITE"),
            ["Hyundai Tucson", "Toyota Vitz"],
        )

    def test_spec_filters_combine_with_the_others(self):
        self.assertEqual(
            self._names("category=cars&spec_fuel=petrol&brand=toyota"),
            ["Toyota Vitz"],
        )
        self.assertEqual(
            self._names("category=cars&condition=used"), ["Hyundai Tucson"]
        )

    def test_bad_spec_params_are_field_errors(self):
        for query, field in (
            ("category=cars&spec_wings=2", "spec_wings"),
            ("category=cars&spec_year=2015", "spec_year"),
            ("category=cars&spec_fuel_min=1", "spec_fuel"),
            ("category=cars&spec_year_min=soon", "spec_year_min"),
            ("spec_fuel=diesel", "category"),
            ("category=vehicles&spec_fuel=diesel", "category"),
            ("condition=broken", "condition"),
        ):
            with self.subTest(query=query):
                response = self.client.get(f"{URL}?{query}")
                self.assertEqual(response.status_code, 400)
                self.assertIn(field, response.data)

    def test_facets_count_each_option_without_its_own_filter(self):
        response = self.client.get(f"{FACETS}?category=cars&spec_fuel=diesel")
        self.assertEqual(response.status_code, 200)
        data = response.data
        self.assertEqual(data["total"], 1)
        # Ticking "diesel" must not hide "petrol" from the fuel options...
        fuel = {v["value"]: v["count"] for v in data["specs"]["fuel"]["values"]}
        self.assertEqual(fuel, {"petrol": 2, "diesel": 1})
        # ...while every other facet does follow it.
        self.assertEqual(data["brands"], [{"value": "Toyota", "count": 1}])
        self.assertEqual(data["specs"]["year"]["range"], {"min": 2019, "max": 2019})

    def test_facets_give_the_price_range_of_the_category_ignoring_its_own_bounds(self):
        Product.objects.filter(name="Boxer").update(price="140000.00")
        Product.objects.filter(name="Vitz").update(price="1500000.00")
        response = self.client.get(f"{FACETS}?category=motorcycles&max_price=100")
        # Motorcycles only, and the price bound itself does not narrow it.
        self.assertEqual(response.data["price"], {"min": 140000.0, "max": 140000.0})

    def test_facets_on_a_parent_count_its_subcategories(self):
        response = self.client.get(f"{FACETS}?category=vehicles")
        self.assertEqual(response.status_code, 200)
        subs = {s["slug"]: s["count"] for s in response.data["subcategories"]}
        self.assertEqual(subs, {"cars": 3, "motorcycles": 1})
        # A parent has no schema, so no spec facets.
        self.assertEqual(response.data["specs"], {})
        # Every category counted regardless of the picked one; parents sum
        # their subcategories.
        self.assertEqual(
            response.data["categories"], {"cars": 3, "motorcycles": 1, "vehicles": 4}
        )
        self.assertEqual(response.data["category_total"], 4)
        conditions = {c["value"]: c["count"] for c in response.data["conditions"]}
        self.assertEqual(conditions, {"new": 3, "used": 1})
