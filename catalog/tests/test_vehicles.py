from django.core.cache import cache
from django.test import SimpleTestCase, TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from catalog.management.commands.seed_shop_data import image_pool_key
from catalog.models import Category, Product
from catalog.taxonomy import ATTRIBUTES, PRODUCT_TYPES
from catalog.taxonomy.sync import sync_taxonomy
from shops.models import Shop
from users.models import User

COMPLETE_CAR = {
    "body_type": "sedan",
    "year": 2015,
    "transmission": "automatic",
    "fuel": "petrol",
    "color": "white",
}


def _category(slug, name, parent=None, order=0):
    category, _ = Category.objects.get_or_create(
        slug=slug, defaults={"name": name, "parent": parent, "display_order": order}
    )
    return category


class SeedImagePoolTest(SimpleTestCase):
    def test_the_photo_pool_follows_the_vehicle_kind(self):
        bus = image_pool_key("trucks-heavy", {"vehicle_kind": "bus"})
        self.assertEqual(bus, "trucks-heavy:bus")
        tyre = image_pool_key("auto-parts", {"part_type": "tyres"})
        self.assertEqual(tyre, "auto-parts:tyres")
        # No pool of its own: the category's.
        lights = image_pool_key("auto-parts", {"part_type": "lights"})
        self.assertEqual(lights, "auto-parts")
        self.assertEqual(image_pool_key("phones", {"storage": 128}), "phones")


class VehicleTaxonomyTest(TestCase):
    def setUp(self):
        vehicles = _category("vehicles", "Vehicles & Auto")
        for order, (slug, name) in enumerate(
            (
                ("cars", "Cars"),
                ("motorcycles", "Motorcycles & Scooters"),
                ("bicycles", "Bicycles & E-bikes"),
                ("three-wheelers", "Three-wheelers"),
                ("trucks-heavy", "Trucks & Heavy"),
                ("auto-parts", "Parts & Accessories"),
            )
        ):
            _category(slug, name, parent=vehicles, order=order)
        sync_taxonomy(ATTRIBUTES, PRODUCT_TYPES)

        owner = User.objects.create_user(username="dealer", password="pass")
        self.shop = Shop.objects.create(name="Car Dealer", owner=owner)
        self.client = APIClient()
        self.client.force_authenticate(owner)
        self.url = f"/api/v1/shops/{self.shop.slug}/products/"

    def _post(self, category="cars", condition="new", specs=None, name="Vitz"):
        payload = {
            "brand": "Toyota",
            "name": name,
            "price": "1500000.00",
            "category": category,
            "condition": condition,
            "specs": COMPLETE_CAR if specs is None else specs,
        }
        return self.client.post(self.url, payload, format="json")

    def test_gas_and_plug_in_cars_can_be_listed(self):
        # An LPG or CNG car has an engine: its gearbox is still required.
        lpg = {**COMPLETE_CAR, "fuel": "lpg"}
        del lpg["transmission"]
        response = self._post(specs=lpg)
        self.assertEqual(response.status_code, 400)
        self.assertIn("transmission", response.data["specs"])
        self.assertEqual(
            self._post(specs={**COMPLETE_CAR, "fuel": "cng"}).status_code, 201
        )
        # A plug-in hybrid has both an engine and a battery worth comparing.
        phev = {
            **COMPLETE_CAR,
            "fuel": "plug_in_hybrid",
            "battery_kwh": 18,
            "range_km": 80,
        }
        self.assertEqual(self._post(specs=phev, name="Prius PHV").status_code, 201)
        # LNG is a truck and bus fuel, not offered for cars.
        lng = self._post(specs={**COMPLETE_CAR, "fuel": "lng"}, name="Odd")
        self.assertEqual(lng.status_code, 400)

    def test_each_subcategory_gets_its_product_type(self):
        linked = dict(
            Category.objects.filter(parent__slug="vehicles").values_list(
                "slug", "product_type__key"
            )
        )
        self.assertEqual(
            linked,
            {
                "cars": "car",
                "motorcycles": "motorcycle",
                "bicycles": "bicycle",
                "three-wheelers": "three_wheeler",
                "trucks-heavy": "heavy_vehicle",
                "auto-parts": "auto_part",
            },
        )

    def test_car_schema_offers_vehicle_colors_only(self):
        response = APIClient().get("/api/v1/catalog/categories/cars/schema/")
        self.assertEqual(response.status_code, 200)
        fields = {f["key"]: f for f in response.data["fields"]}
        self.assertEqual(
            set(fields),
            {
                "body_type",
                "year",
                "transmission",
                "fuel",
                "engine_size",
                "motor_power",
                "battery_kwh",
                "range_km",
                "drivetrain",
                "seats",
                "color",
                "mileage",
            },
        )
        colors = {o["value"] for o in fields["color"]["options"]}
        self.assertIn("white", colors)
        self.assertNotIn("natural_titanium", colors)

    def test_a_complete_car_is_accepted(self):
        response = self._post()
        self.assertEqual(response.status_code, 201, response.data)

    def test_an_electric_car_is_not_asked_for_an_engine_or_gearbox(self):
        electric = {
            "body_type": "suv",
            "year": 2024,
            "fuel": "electric",
            "color": "White",
            "battery_kwh": 60,
            "range_km": 420,
        }
        response = self._post(category="cars", specs=electric, name="Atto 3")
        self.assertEqual(response.status_code, 201, response.data)

        # A combustion spec on an EV does not apply here.
        response = self._post(
            category="cars",
            specs={**electric, "engine_size": 1.5, "transmission": "automatic"},
            name="Atto 3",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("engine_size", response.data["specs"])
        self.assertIn("transmission", response.data["specs"])

    def test_a_used_vehicle_needs_its_mileage(self):
        response = self._post(condition="used")
        self.assertEqual(response.status_code, 400)
        self.assertIn("mileage", response.data["specs"])

        specs = {**COMPLETE_CAR, "mileage": 85000}
        response = self._post(condition="used", specs=specs)
        self.assertEqual(response.status_code, 201, response.data)

    def test_load_capacity_applies_to_trucks_only(self):
        base = {
            "year": 2018,
            "transmission": "manual",
            "fuel": "diesel",
            "load_capacity": 10,
        }
        response = self._post(
            category="trucks-heavy", specs={**base, "vehicle_kind": "truck"}
        )
        self.assertEqual(response.status_code, 201, response.data)

        response = self._post(
            category="trucks-heavy",
            specs={**base, "vehicle_kind": "bus"},
            name="Coaster",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("load_capacity", response.data["specs"])

    def test_the_parent_category_lists_its_subcategories_products(self):
        self._post()  # a car
        self._post(category="auto-parts", specs={"part_type": "tyres"}, name="Tyre")
        response = APIClient().get("/api/v1/catalog/products/?category=vehicles")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 2)

    def test_public_stats_count_subcategory_products_in_the_parent(self):
        for name, slug in (("Vitz", "cars"), ("Boxer", "motorcycles")):
            Product.objects.create(
                shop=self.shop,
                category=Category.objects.get(slug=slug),
                brand="Toyota",
                name=name,
                price="1000.00",
                last_confirmed_at=timezone.now(),
            )
        # The view is cached for a day; another test may have filled it.
        cache.clear()
        response = APIClient().get("/api/v1/catalog/public-stats/")
        self.assertEqual(response.status_code, 200)
        counts = response.data["category_product_count"]
        self.assertEqual((counts["cars"], counts["motorcycles"]), (1, 1))
        self.assertEqual(counts["vehicles"], 2)

    def test_the_category_list_nests_subcategories(self):
        response = APIClient().get("/api/v1/catalog/categories/")
        self.assertEqual(response.status_code, 200)
        by_slug = {c["slug"]: c for c in response.data["results"]}
        vehicles = by_slug["vehicles"]
        self.assertFalse(vehicles["has_specs"])
        self.assertEqual(
            [c["slug"] for c in vehicles["children"]],
            [
                "cars",
                "motorcycles",
                "bicycles",
                "three-wheelers",
                "trucks-heavy",
                "auto-parts",
            ],
        )
        self.assertTrue(vehicles["children"][0]["has_specs"])
        # Subcategories are still listed on their own, as before.
        self.assertEqual(by_slug["cars"]["parent"], vehicles["id"])

    def test_a_listing_cannot_go_in_a_parent_category(self):
        response = self._post(category="vehicles")
        self.assertEqual(response.status_code, 400)
        self.assertIn("category", response.data)

    def test_a_bicycle_is_listed_without_engine_specs(self):
        specs = {
            "bike_type": "electric",
            "wheel_size": 26,
            "color": "Gray",
            "motor_power": 250,
        }
        response = self._post(category="bicycles", specs=specs, name="HIMO C26")
        self.assertEqual(response.status_code, 201, response.data)
        # A bicycle has no mileage, even used: the field is not in its schema.
        response = self._post(
            category="bicycles",
            condition="used",
            specs={**specs, "mileage": 1200},
            name="HIMO C26 used",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("mileage", response.data["specs"])

    def test_an_electric_scooter_needs_no_engine_but_a_petrol_one_does(self):
        electric = {
            "motorcycle_type": "scooter",
            "fuel": "electric",
            "year": 2024,
            "motor_power": 1200,
            "range_km": 70,
            "color": "White",
        }
        response = self._post(category="motorcycles", specs=electric, name="G5")
        self.assertEqual(response.status_code, 201, response.data)

        # Petrol: the engine size and gearbox are back, and required.
        petrol = {**electric, "fuel": "petrol"}
        del petrol["motor_power"], petrol["range_km"]
        response = self._post(category="motorcycles", specs=petrol, name="NMAX")
        self.assertEqual(response.status_code, 400)
        self.assertIn("engine_cc", response.data["specs"])
        self.assertIn("transmission", response.data["specs"])

        # And an electric-only spec does not apply to a petrol bike.
        petrol.update(engine_cc=155, transmission="automatic", motor_power=1200)
        response = self._post(category="motorcycles", specs=petrol, name="NMAX")
        self.assertEqual(response.status_code, 400)
        self.assertIn("motor_power", response.data["specs"])

    def test_an_electric_motorcycle_needs_its_motor_power(self):
        # The electric counterpart to engine_cc: the power rating is required
        # once the power source is electric, the way cc is for petrol.
        specs = {
            "motorcycle_type": "scooter",
            "fuel": "electric",
            "year": 2024,
            "color": "White",
        }
        response = self._post(category="motorcycles", specs=specs, name="G5")
        self.assertEqual(response.status_code, 400)
        self.assertIn("motor_power", response.data["specs"])

        specs["motor_power"] = 1200
        response = self._post(category="motorcycles", specs=specs, name="G5")
        self.assertEqual(response.status_code, 201, response.data)

    def test_used_electric_battery_health_is_recommended_not_required(self):
        # A used electric still publishes without its battery health: the field
        # is recommended, so it never blocks.
        specs = {
            "motorcycle_type": "scooter",
            "fuel": "electric",
            "year": 2023,
            "motor_power": 1200,
            "mileage": 4200,
            "color": "White",
        }
        response = self._post(
            category="motorcycles", condition="used", specs=specs, name="G5"
        )
        self.assertEqual(response.status_code, 201, response.data)

    def test_motorcycle_schema_offers_the_electric_battery_specs(self):
        response = APIClient().get("/api/v1/catalog/categories/motorcycles/schema/")
        self.assertEqual(response.status_code, 200)
        fields = {f["key"]: f for f in response.data["fields"]}
        self.assertEqual(fields["motor_power"]["required_if"], {"fuel": ["electric"]})
        self.assertEqual(fields["battery_kwh"]["show_if"], {"fuel": ["electric"]})
        self.assertTrue(fields["battery_kwh"]["recommended"])
        self.assertFalse(fields["battery_kwh"]["required"])
        self.assertEqual(
            fields["battery_health"]["show_if"],
            {"condition": ["used"], "fuel": ["electric"]},
        )

    def test_an_e_bike_needs_its_motor_power(self):
        specs = {"bike_type": "electric", "wheel_size": 26, "color": "Gray"}
        response = self._post(category="bicycles", specs=specs, name="HIMO C26")
        self.assertEqual(response.status_code, 400)
        self.assertIn("motor_power", response.data["specs"])

        response = self._post(
            category="bicycles",
            specs={**specs, "motor_power": 250, "battery_kwh": 0.5, "range_km": 60},
            name="HIMO C26",
        )
        self.assertEqual(response.status_code, 201, response.data)

        # A plain bicycle is not asked for a motor.
        response = self._post(
            category="bicycles",
            specs={"bike_type": "mountain", "wheel_size": 29, "color": "Black"},
            name="Talon 3",
        )
        self.assertEqual(response.status_code, 201, response.data)

    def test_a_three_wheeler_switches_between_engine_and_motor(self):
        petrol = {
            "vehicle_use": "passenger",
            "fuel": "petrol",
            "year": 2023,
            "color": "Black",
        }
        response = self._post(category="three-wheelers", specs=petrol, name="RE 4S")
        self.assertEqual(response.status_code, 400)
        self.assertIn("engine_cc", response.data["specs"])
        self.assertIn("transmission", response.data["specs"])

        petrol.update(engine_cc=236, transmission="manual")
        response = self._post(category="three-wheelers", specs=petrol, name="RE 4S")
        self.assertEqual(response.status_code, 201, response.data)

        electric = {
            "vehicle_use": "passenger",
            "fuel": "electric",
            "year": 2024,
            "color": "Blue",
            "motor_power": 1200,
            "battery_kwh": 3,
        }
        response = self._post(category="three-wheelers", specs=electric, name="King EV")
        self.assertEqual(response.status_code, 201, response.data)

        # The petrol specs do not apply to the electric one.
        response = self._post(
            category="three-wheelers",
            specs={**electric, "engine_cc": 236},
            name="King EV",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("engine_cc", response.data["specs"])

    def test_used_machinery_is_measured_in_hours_not_kilometres(self):
        base = {
            "vehicle_kind": "machinery",
            "year": 2016,
            "transmission": "automatic",
            "fuel": "diesel",
        }
        response = self._post(
            category="trucks-heavy", condition="used", specs=base, name="EC210D"
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("operating_hours", response.data["specs"])
        self.assertNotIn("mileage", response.data["specs"])

        specs = {**base, "operating_hours": 4200}
        response = self._post(
            category="trucks-heavy", condition="used", specs=specs, name="EC210D"
        )
        self.assertEqual(response.status_code, 201, response.data)

    def test_seats_apply_to_buses_not_trucks(self):
        base = {"year": 2019, "transmission": "manual", "fuel": "diesel"}
        response = self._post(
            category="trucks-heavy",
            specs={**base, "vehicle_kind": "bus", "seats": 30},
            name="Coaster",
        )
        self.assertEqual(response.status_code, 201, response.data)
        response = self._post(
            category="trucks-heavy",
            specs={**base, "vehicle_kind": "truck", "seats": 3},
            name="FSR",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("seats", response.data["specs"])

    def test_a_part_needs_only_its_type(self):
        response = self._post(
            category="auto-parts", specs={"part_type": "tyres"}, name="Tyre 195/65"
        )
        self.assertEqual(response.status_code, 201, response.data)
