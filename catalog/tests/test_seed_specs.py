import io
import tempfile

from django.core.management import call_command
from django.test import TestCase, override_settings

from catalog.models import Category, Product
from catalog.seed_specs import ensure_taxonomy, seed_specs
from catalog.specs import category_schema, validate_specs

MEDIA = tempfile.mkdtemp()


class SeedSpecsTest(TestCase):
    def setUp(self):
        for slug in ("phones", "laptops", "audio", "cameras", "accessories"):
            Category.objects.create(name=slug.title(), slug=slug)
        ensure_taxonomy()

    def _cat(self, slug):
        return Category.objects.get(slug=slug)

    def test_the_name_supplies_what_it_says(self):
        specs = seed_specs(
            self._cat("laptops"),
            {},
            text="Apple MacBook Pro 14 M3 16GB 512GB Space Black",
            condition="new",
            seed="mbp",
        )
        # Typed field: stored as a person would type it, not as an option key.
        self.assertEqual(specs["processor"], "Apple M3")
        self.assertEqual(specs["ram"], 16)
        self.assertEqual(specs["storage"], 512)
        # The name does not say, so the mainstream assumption: SSD.
        self.assertEqual(specs["storage_type"], "ssd")
        self.assertNotIn("secondary_storage", specs)
        self.assertTrue(13 <= specs["screen_size"] <= 16)
        # Pick-or-type fields store the label a merchant would pick.
        self.assertEqual(specs["warranty"], "Brand · 1 year")
        self.assertEqual(specs["color"], "Black")

    def test_a_single_size_in_a_phone_name_is_storage(self):
        specs = seed_specs(
            self._cat("phones"),
            {},
            text="Apple iPhone 13 128GB Midnight",
            condition="new",
            seed="ip13",
        )
        self.assertEqual(specs["storage"], 128)
        self.assertEqual(specs["color"], "Black")
        # Not stated in the name, so invented -- and never a budget size.
        self.assertIn(specs["ram"], {4, 6, 8, 12})

    def test_a_budget_size_in_the_name_is_kept(self):
        specs = seed_specs(
            self._cat("phones"),
            {},
            text="Itel A70 2GB 32GB",
            condition="new",
            seed="itel",
        )
        self.assertEqual(specs["ram"], 2)
        self.assertEqual(specs["storage"], 32)

    def test_a_dual_drive_laptop_gets_both_sizes(self):
        specs = seed_specs(
            self._cat("laptops"),
            {"storage": "256GB SSD + 1TB HDD"},
            text="Lenovo Legion 5 Ryzen 7 16GB 256GB 1TB",
            condition="new",
            seed="legion",
        )
        self.assertEqual(specs["storage_type"], "ssd_hdd")
        self.assertIn("secondary_storage", specs)
        self.assertEqual(specs["processor"], "AMD Ryzen 7")
        self.assertEqual(specs["storage"], 256)

    def test_the_laptop_type_comes_from_the_model(self):
        def laptop_type(name):
            return seed_specs(
                self._cat("laptops"), {}, text=name, condition="new", seed=name
            )["laptop_type"]

        self.assertEqual(laptop_type("Lenovo Legion 5 Pro RTX 3070"), "Gaming")
        self.assertEqual(laptop_type("Dell Latitude 5420 Core i5"), "Business")
        # "RTX A2000" is a workstation card, not a gaming one.
        self.assertEqual(
            laptop_type("Dell Precision 5560 Mobile Workstation RTX A2000"),
            "Workstation",
        )
        self.assertEqual(laptop_type("HP Spectre x360 2-in-1 14 OLED"), "2-in-1")
        # The name does not say and the field is optional: left empty.
        specs = seed_specs(
            self._cat("laptops"),
            {},
            text="HP Pavilion 15 Core i5",
            condition="new",
            seed="pavilion",
        )
        self.assertNotIn("laptop_type", specs)

    def test_used_items_get_their_condition_fields(self):
        specs = seed_specs(
            self._cat("phones"),
            {},
            text="Samsung Galaxy S23 256GB Phantom Black",
            condition="used",
            seed="s23",
        )
        self.assertTrue(78 <= specs["battery_health"] <= 99)
        self.assertEqual(specs["warranty"], "Shop · 3 months")

    def test_type_is_read_before_the_fields_that_depend_on_it(self):
        specs = seed_specs(
            self._cat("audio"),
            {},
            text="JBL Boombox 3 Wi-Fi & Bluetooth Speaker",
            condition="new",
            seed="boombox",
        )
        self.assertEqual(specs["audio_type"], "speaker")
        self.assertEqual(specs["connectivity"], "bluetooth")
        self.assertNotIn("noise_cancellation", specs)

    def test_the_lens_mount_follows_the_brand(self):
        specs = seed_specs(
            self._cat("cameras"),
            {},
            text="Canon EOS R6 Mark II Mirrorless with 24-105mm",
            condition="new",
            seed="r6",
        )
        self.assertEqual(specs["camera_type"], "mirrorless")
        self.assertEqual(specs["kit"], "with_lens")
        self.assertEqual(specs["lens_mount"], "Canon RF")

    def test_old_free_text_specs_are_converted_then_dropped(self):
        specs = seed_specs(
            self._cat("accessories"),
            {"capacity": "10000mAh", "type": "Power bank", "port": "USB-C + USB-A"},
            text="Oraimo PowerBank 10000mAh 10000mAh, slim",
            condition="new",
            seed="pb",
        )
        self.assertEqual(specs["accessory_type"], "power_bank")
        self.assertEqual(specs["battery_capacity"], 10000)
        self.assertIn("output_power", specs)
        self.assertNotIn("capacity", specs)

    def test_only_schema_keys_are_kept(self):
        specs = seed_specs(
            self._cat("phones"),
            {"display": "6.2″ Dynamic AMOLED 2X 120Hz", "n5g": "Yes", "year": "2024"},
            text="Samsung Galaxy S24 128GB 8GB Cobalt Violet",
            condition="new",
            seed="s24",
        )
        self.assertEqual(specs["screen_size"], 6.2)
        self.assertEqual(specs["network"], "5g")
        # Old keys are gone, whether or not a field took their value.
        for old in ("display", "n5g", "year"):
            self.assertNotIn(old, specs)
        # Required unit facts get the mainstream defaults.
        self.assertEqual(specs["carrier_lock"], "unlocked")

    def test_a_used_vehicle_gets_mileage_and_a_new_one_does_not(self):
        call_command("seed_catalog", stdout=io.StringIO())
        cars = self._cat("cars")
        base = {
            "body_type": "sedan",
            "year": 2015,
            "transmission": "manual",
            "fuel": "petrol",
        }
        used = seed_specs(
            cars, base, text="Toyota Corolla White", condition="used", seed="c1"
        )
        self.assertTrue(12000 <= used["mileage"] <= 180000)
        self.assertEqual(used["color"], "White")
        new = seed_specs(
            cars, base, text="Toyota Corolla White", condition="new", seed="c2"
        )
        self.assertNotIn("mileage", new)

    def test_the_same_seed_gives_the_same_specs(self):
        args = {"text": "Tecno Spark 10 Pro", "condition": "new", "seed": "same"}
        first = seed_specs(self._cat("phones"), {}, **args)
        second = seed_specs(self._cat("phones"), {}, **args)
        self.assertEqual(first, second)


@override_settings(MEDIA_ROOT=MEDIA)
class SeedCommandsProduceValidSpecsTest(TestCase):
    def test_every_sample_product_passes_its_schema(self):
        call_command("seed_catalog", verbosity=0)
        call_command("seed_shop_data", "--sample", "--no-images", verbosity=0)

        products = Product.objects.select_related("category__product_type")
        self.assertTrue(products.exists())
        for product in products:
            schema = category_schema(product.category)
            self.assertIsNotNone(schema, product.category.slug)
            defined = {f["key"] for f in schema["fields"]}
            legacy = {k: v for k, v in product.specs.items() if k not in defined}
            errors = validate_specs(
                schema, product.specs, condition=product.condition, existing=legacy
            )
            self.assertEqual(errors, {}, product.slug)
