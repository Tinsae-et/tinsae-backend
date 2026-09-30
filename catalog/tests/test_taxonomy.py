import copy
import io
import tempfile

from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from PIL import Image
from rest_framework.test import APIClient

from catalog.models import (
    AttributeOption,
    Category,
    Product,
    ProductImage,
    ProductType,
)
from catalog.specs import category_schema, size_to_gb, validate_specs
from catalog.specs_migration import migrate_product_specs
from catalog.taxonomy import ATTRIBUTES, PRODUCT_TYPES
from catalog.taxonomy.sync import TaxonomyError, sync_taxonomy
from shops.models import Shop
from users.models import User

COMPLETE_PHONE = {
    "phone_type": "smartphone",
    "storage": 256,
    "ram": 8,
    "color": "black",
    "sim": "dual",
    "network": "5g",
    "carrier_lock": "unlocked",
    "region_version": "Global",
    "warranty": "brand_1y",
}


def _applied(changes):
    return [c for c in changes if not c.startswith("note:")]


class SyncTaxonomyTest(TestCase):
    def setUp(self):
        self.phones = Category.objects.create(name="Phones", slug="phones")

    def test_sync_links_categories_and_is_idempotent(self):
        first = sync_taxonomy(ATTRIBUTES, PRODUCT_TYPES)
        self.assertTrue(_applied(first))
        self.phones.refresh_from_db()
        self.assertEqual(self.phones.product_type.key, "smartphone")

        second = sync_taxonomy(ATTRIBUTES, PRODUCT_TYPES)
        self.assertEqual(_applied(second), [])

    def test_dry_run_writes_nothing(self):
        changes = sync_taxonomy(ATTRIBUTES, PRODUCT_TYPES, dry_run=True)
        self.assertTrue(_applied(changes))
        self.assertFalse(ProductType.objects.exists())
        self.phones.refresh_from_db()
        self.assertIsNone(self.phones.product_type)

    def test_an_attribute_type_never_narrows(self):
        sync_taxonomy(ATTRIBUTES, PRODUCT_TYPES)
        attributes = copy.deepcopy(ATTRIBUTES)
        attributes["battery_cycles"]["type"] = "text"
        with self.assertRaises(TaxonomyError):
            sync_taxonomy(attributes, PRODUCT_TYPES)

    def test_an_enum_may_widen_to_text(self):
        # Processor started as a closed list; every stored value stays valid text.
        as_enum = copy.deepcopy(ATTRIBUTES)
        as_enum["processor"]["type"] = "enum"
        sync_taxonomy(as_enum, PRODUCT_TYPES)
        changes = sync_taxonomy(ATTRIBUTES, PRODUCT_TYPES)
        self.assertIn("widen attribute processor: enum -> text", changes)
        self.assertEqual(_applied(sync_taxonomy(ATTRIBUTES, PRODUCT_TYPES)), [])

    def test_a_dropped_option_is_deactivated_not_deleted(self):
        sync_taxonomy(ATTRIBUTES, PRODUCT_TYPES)
        attributes = copy.deepcopy(ATTRIBUTES)
        attributes["network"]["options"] = [{"value": "5g", "label": "5G"}]
        sync_taxonomy(attributes, PRODUCT_TYPES)
        option = AttributeOption.objects.get(attribute__key="network", value="4g")
        self.assertFalse(option.is_active)

    def test_definitions_are_checked_before_writing(self):
        types = copy.deepcopy(PRODUCT_TYPES)
        types["smartphone"]["fields"][0]["options"] = ["3tb"]
        with self.assertRaises(TaxonomyError):
            sync_taxonomy(ATTRIBUTES, types)
        self.assertFalse(ProductType.objects.exists())


class CategorySchemaApiTest(TestCase):
    def setUp(self):
        Category.objects.create(name="Phones", slug="phones")
        Category.objects.create(name="Misc", slug="misc")
        sync_taxonomy(ATTRIBUTES, PRODUCT_TYPES)

    def test_schema_lists_fields_in_order_with_their_inputs(self):
        response = APIClient().get("/api/v1/catalog/categories/phones/schema/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["product_type"], "smartphone")
        keys = [f["key"] for f in response.data["fields"]]
        self.assertEqual(keys[:3], ["phone_type", "storage", "ram"])
        storage = next(f for f in response.data["fields"] if f["key"] == "storage")
        # A typed quantity in GB with a GB/TB switch, not a list of sizes.
        self.assertEqual(
            (storage["type"], storage["unit"], storage["control"]),
            ("number", "GB", "size"),
        )
        self.assertIsNone(storage["options"])
        sim = next(f for f in response.data["fields"] if f["key"] == "sim")
        self.assertEqual(sim["type"], "enum")
        battery = next(
            f for f in response.data["fields"] if f["key"] == "battery_health"
        )
        self.assertEqual(battery["show_if"], {"condition": ["used"]})
        self.assertEqual(battery["max"], 100)

    def test_fields_carry_their_section_and_level(self):
        response = APIClient().get("/api/v1/catalog/categories/phones/schema/")
        fields = {f["key"]: f for f in response.data["fields"]}
        # Key specs are required; the spec sheet is recommended, never required;
        # facts about the unit sit in their own section.
        self.assertEqual(
            (fields["storage"]["section"], fields["storage"]["required"]), ("key", True)
        )
        self.assertEqual(
            (fields["chipset"]["section"], fields["chipset"]["recommended"]),
            ("details", True),
        )
        self.assertFalse(fields["chipset"]["required"])
        self.assertEqual(
            (fields["carrier_lock"]["section"], fields["carrier_lock"]["required"]),
            ("item", True),
        )

    def test_recommended_fields_do_not_block_publishing(self):
        # COMPLETE_PHONE has no chipset, camera or battery: still publishable.
        category = Category.objects.get(slug="phones")
        errors = validate_specs(
            category_schema(category), COMPLETE_PHONE, condition="new"
        )
        self.assertEqual(errors, {})

    def test_a_field_is_not_both_required_and_recommended(self):
        types = copy.deepcopy(PRODUCT_TYPES)
        types["smartphone"]["fields"][1] = {
            **types["smartphone"]["fields"][1],
            "recommended": True,
        }
        with self.assertRaises(TaxonomyError):
            sync_taxonomy(ATTRIBUTES, types)

    def test_category_without_a_type_has_no_schema(self):
        response = APIClient().get("/api/v1/catalog/categories/misc/schema/")
        self.assertEqual(response.status_code, 404)

    def test_unknown_category_is_404(self):
        response = APIClient().get("/api/v1/catalog/categories/nope/schema/")
        self.assertEqual(response.status_code, 404)


class ListingSpecValidationTest(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username="owner", password="pass")
        self.shop = Shop.objects.create(name="Spec Shop", owner=self.owner)
        self.phones = Category.objects.create(name="Phones", slug="phones")
        self.misc = Category.objects.create(name="Misc", slug="misc")
        sync_taxonomy(ATTRIBUTES, PRODUCT_TYPES)
        self.client = APIClient()
        self.client.force_authenticate(self.owner)
        self.url = f"/api/v1/shops/{self.shop.slug}/products/"

    def _payload(self, **overrides):
        payload = {
            "brand": "Apple",
            "name": "iPhone 15",
            "price": "120000.00",
            "category": "phones",
            "condition": "new",
            "specs": dict(COMPLETE_PHONE),
        }
        payload.update(overrides)
        return payload

    def test_complete_specs_are_accepted(self):
        response = self.client.post(self.url, self._payload(), format="json")
        self.assertEqual(response.status_code, 201, response.data)

    def test_missing_required_specs_are_field_errors(self):
        response = self.client.post(
            self.url, self._payload(specs={"storage": 256}), format="json"
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("ram", response.data["specs"])
        self.assertIn("warranty", response.data["specs"])

    def test_any_size_is_accepted_but_it_must_be_a_number(self):
        # 480GB is not a "standard" size and is still a real one.
        specs = {**COMPLETE_PHONE, "storage": 480, "ram": 3}
        response = self.client.post(self.url, self._payload(specs=specs), format="json")
        self.assertEqual(response.status_code, 201, response.data)

        specs = {**COMPLETE_PHONE, "storage": "300gb", "sim": "quad"}
        response = self.client.post(
            self.url, self._payload(name="Other", specs=specs), format="json"
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("storage", response.data["specs"])
        self.assertIn("sim", response.data["specs"])

    def test_pick_or_type_values_are_tidied_to_the_suggestion(self):
        # Typed "  black " is the suggestion "Black"; a colour the list does
        # not have is kept as typed; free text elsewhere is trimmed too.
        specs = {**COMPLETE_PHONE, "color": "  black ", "warranty": "Shop · 18 months"}
        response = self.client.post(self.url, self._payload(specs=specs), format="json")
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["specs"]["color"], "Black")
        self.assertEqual(response.data["specs"]["warranty"], "Shop · 18 months")

        specs = {**COMPLETE_PHONE, "color": "Titanium   Blue"}
        response = self.client.post(
            self.url, self._payload(name="Blue one", specs=specs), format="json"
        )
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["specs"]["color"], "Titanium Blue")

    def test_a_blank_typed_value_does_not_satisfy_a_required_field(self):
        specs = {**COMPLETE_PHONE, "color": "   "}
        response = self.client.post(self.url, self._payload(specs=specs), format="json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("color", response.data["specs"])

    def test_a_used_phone_needs_its_battery_health(self):
        response = self.client.post(
            self.url, self._payload(condition="used"), format="json"
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("battery_health", response.data["specs"])

        specs = {**COMPLETE_PHONE, "battery_health": 91}
        response = self.client.post(
            self.url, self._payload(condition="used", specs=specs), format="json"
        )
        self.assertEqual(response.status_code, 201, response.data)

    def test_unknown_keys_are_rejected_on_a_new_listing(self):
        specs = {**COMPLETE_PHONE, "colour": "black"}
        response = self.client.post(self.url, self._payload(specs=specs), format="json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("colour", response.data["specs"])

    def test_a_draft_may_be_incomplete_but_not_invalid(self):
        response = self.client.post(
            self.url,
            self._payload(status="draft", specs={"storage": 256}),
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.data)
        response = self.client.post(
            self.url,
            self._payload(status="draft", name="Other", specs={"storage": "300gb"}),
            format="json",
        )
        self.assertEqual(response.status_code, 400)

    def test_older_listings_stay_editable(self):
        product = Product.objects.create(
            shop=self.shop,
            category=self.phones,
            brand="Apple",
            name="Legacy",
            price="1000.00",
            specs={"display": '6.1" OLED', "storage": "256GB"},
        )
        detail = f"{self.url}{product.slug}/"
        # A price change does not touch specs, so it is not blocked.
        response = self.client.patch(detail, {"price": "900.00"}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        # Legacy keys and unchanged legacy values pass; required fields do not.
        specs = {**COMPLETE_PHONE, "display": '6.1" OLED', "storage": "256GB"}
        response = self.client.patch(detail, {"specs": specs}, format="json")
        self.assertEqual(response.status_code, 200, response.data)

    def test_a_category_without_a_type_takes_free_form_specs(self):
        response = self.client.post(
            self.url,
            self._payload(category="misc", specs={"anything": "goes"}),
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.data)


class SpecsMigrationTest(TestCase):
    def setUp(self):
        for slug in ("phones", "laptops", "audio"):
            Category.objects.create(name=slug.title(), slug=slug)
        sync_taxonomy(ATTRIBUTES, PRODUCT_TYPES)

    def _schema(self, slug):
        return category_schema(Category.objects.get(slug=slug))

    def test_phone_values_are_mapped_and_legacy_keys_kept(self):
        before = {
            "storage": "256GB",
            "ram": "8GB",
            "sim": "Dual Nano-SIM",
            "n5g": "Yes",
            "display": '6.1" OLED',
        }
        after = migrate_product_specs("phones", self._schema("phones"), before, "new")
        self.assertEqual(after["storage"], 256)
        self.assertEqual(after["ram"], 8)
        self.assertEqual(after["sim"], "dual")
        self.assertEqual(after["network"], "5g")
        self.assertEqual(after["display"], '6.1" OLED')

    def test_laptop_free_text_is_parsed(self):
        before = {
            "storage": "512GB SSD",
            "ram": "16GB LPDDR5x",
            "processor": "Apple M3 Pro 11-core",
            "display": "14.2″ Liquid Retina XDR",
            "os": "macOS Sonoma",
        }
        after = migrate_product_specs("laptops", self._schema("laptops"), before, "new")
        self.assertEqual(after["storage"], 512)
        self.assertEqual(after["ram"], 16)
        self.assertEqual(after["storage_type"], "ssd")
        # Processor is typed text now: the exact model is kept as written.
        self.assertEqual(after["processor"], "Apple M3 Pro 11-core")
        self.assertEqual(after["screen_size"], 14.2)
        # os is pick-or-type text: a valid free-text value is kept as typed.
        self.assertEqual(after["os"], "macOS Sonoma")

    def test_type_decides_which_fields_apply(self):
        before = {"type": "Portable Stereo Speaker", "anc": "Yes", "bluetooth": "5.3"}
        after = migrate_product_specs("audio", self._schema("audio"), before, "new")
        self.assertEqual(after["audio_type"], "speaker")
        # Noise cancelling only applies to headphones and earbuds.
        self.assertNotIn("noise_cancellation", after)

    def test_a_valid_value_is_never_overwritten(self):
        before = {"storage": 128, "sim": "Dual Nano-SIM"}
        after = migrate_product_specs("phones", self._schema("phones"), before, "new")
        self.assertEqual(after["storage"], 128)


class SpecSheetConversionTest(TestCase):
    """Old one-line specs split into the structured spec-sheet fields."""

    def setUp(self):
        for slug in ("phones", "laptops"):
            Category.objects.create(name=slug.title(), slug=slug)
        sync_taxonomy(ATTRIBUTES, PRODUCT_TYPES)

    def _schema(self, slug):
        return category_schema(Category.objects.get(slug=slug))

    def test_a_phone_spec_sheet_is_split_into_fields(self):
        before = {
            "display": "6.2″ Dynamic AMOLED 2X 120Hz",
            "chip": "Exynos 2400 Deca-Core",
            "camera": "50MP Triple OIS",
            "battery": "4,000 mAh 25W",
            "year": "2024",
            "weight": "167 g",
        }
        after = migrate_product_specs("phones", self._schema("phones"), before, "new")
        self.assertEqual(after["screen_size"], 6.2)
        self.assertEqual(after["display_panel"], "Dynamic AMOLED 2X")
        self.assertEqual(after["refresh_rate"], 120)
        self.assertEqual(after["chipset"], "Exynos 2400 Deca-Core")
        self.assertEqual((after["main_camera"], after["camera_setup"]), (50, "triple"))
        self.assertEqual(
            (after["battery_capacity"], after["fast_charging"]), (4000, 25)
        )
        self.assertEqual((after["release_year"], after["weight"]), (2024, 167))

    def test_a_laptop_spec_sheet_is_split_into_fields(self):
        before = {
            "ram": "16GB LPDDR5x",
            "gpu": "NVIDIA GeForce RTX 4060 8GB",
            "display": "16″ IPS 165Hz",
            "weight": "2.3 kg",
        }
        after = migrate_product_specs("laptops", self._schema("laptops"), before, "new")
        self.assertEqual(after["ram_type"], "LPDDR5X")
        self.assertEqual((after["gpu_type"], after["vram"]), ("dedicated", 8))
        self.assertEqual((after["display_panel"], after["refresh_rate"]), ("IPS", 165))
        self.assertEqual(after["weight"], 2300)

        integrated = migrate_product_specs(
            "laptops", self._schema("laptops"), {"gpu": "Intel Iris Xe"}, "new"
        )
        self.assertEqual(integrated["gpu_type"], "integrated")
        self.assertNotIn("vram", integrated)


class SizeConversionTest(TestCase):
    """Storage and RAM moved from size lists ("256gb") to numbers in GB."""

    def test_size_to_gb(self):
        self.assertEqual(size_to_gb("256gb"), 256)
        self.assertEqual(size_to_gb("1tb"), 1024)
        self.assertEqual(size_to_gb("512GB SSD"), 512)
        self.assertEqual(size_to_gb("512MB"), 0.5)
        self.assertEqual(size_to_gb(64), 64)
        self.assertIsNone(size_to_gb("unknown"))

    def test_the_sync_converts_stored_values_once(self):
        phones = Category.objects.create(name="Phones", slug="phones")
        owner = User.objects.create_user(username="conv", password="pass")
        shop = Shop.objects.create(name="Conv Shop", owner=owner)
        # The database as it was: storage and RAM were enums of sizes.
        as_enum = copy.deepcopy(ATTRIBUTES)
        for key in ("storage", "secondary_storage", "ram"):
            as_enum[key] = {
                "label": key,
                "type": "enum",
                "options": [{"value": "8gb", "label": "8 GB"}],
            }
        sync_taxonomy(as_enum, PRODUCT_TYPES)
        product = Product.objects.create(
            shop=shop,
            category=phones,
            brand="A",
            name="Old",
            price="1.00",
            specs={"storage": "1tb", "ram": "8gb", "display": "x"},
        )
        odd = Product.objects.create(
            shop=shop,
            category=phones,
            brand="A",
            name="Odd",
            price="1.00",
            specs={"storage": "lots"},
        )

        changes = sync_taxonomy(ATTRIBUTES, PRODUCT_TYPES)

        self.assertTrue(any(c.startswith("convert attribute storage") for c in changes))
        product.refresh_from_db()
        odd.refresh_from_db()
        self.assertEqual(product.specs, {"storage": 1024, "ram": 8, "display": "x"})
        self.assertEqual(odd.specs, {"storage_text": "lots"})
        # Converted once: the next run finds the types already in place.
        self.assertEqual(_applied(sync_taxonomy(ATTRIBUTES, PRODUCT_TYPES)), [])


MEDIA = tempfile.mkdtemp()


def _png():
    buf = io.BytesIO()
    Image.new("RGB", (1, 1)).save(buf, format="PNG")
    return SimpleUploadedFile("px.png", buf.getvalue(), content_type="image/png")


@override_settings(MEDIA_ROOT=MEDIA)
class SharedImageFileTest(TestCase):
    def test_a_file_still_in_use_is_not_deleted(self):
        owner = User.objects.create_user(username="img-owner", password="pass")
        shop = Shop.objects.create(name="Img Shop", owner=owner)
        category = Category.objects.create(name="Phones", slug="phones")
        a, b = (
            Product.objects.create(
                shop=shop, category=category, brand="A", name=n, price="1.00"
            )
            for n in ("One", "Two")
        )
        first = ProductImage.objects.create(product=a, image=_png())
        name = first.image.name
        ProductImage.objects.create(product=b, image=name)

        first.delete()
        self.assertTrue(default_storage.exists(name))

        ProductImage.objects.get(product=b).delete()
        self.assertFalse(default_storage.exists(name))
