import io
import os
import tempfile
from datetime import datetime, timedelta

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone
from PIL import Image

from catalog.freshness import (
    ALMOST_EXPIRED_WINDOW,
    FRESH_WINDOW,
    Freshness,
    get_freshness,
)
from catalog.models import Category, Product, ProductCondition, ProductImage
from catalog.serializers import ProductSerializer
from shops.models import Shop


class CategoryModelTest(TestCase):
    def test_category_cannot_be_its_own_parent(self):
        cat = Category.objects.create(name="Phones", slug="phones")
        cat.parent = cat
        with self.assertRaises(ValidationError):
            cat.full_clean()

    def test_nesting_deeper_than_two_levels_is_rejected(self):
        top = Category.objects.create(name="Electronics", slug="electronics")
        mid = Category.objects.create(name="Phones", slug="phones", parent=top)
        deep = Category(name="Android", slug="android", parent=mid)
        with self.assertRaises(ValidationError):
            deep.full_clean()

    def test_seed_command_is_idempotent(self):
        from django.core.management import call_command

        call_command("seed_catalog")
        count = Category.objects.count()
        call_command("seed_catalog")
        self.assertEqual(Category.objects.count(), count)

    def test_seed_shop_data_sample_mode(self):
        from django.core.management import call_command

        call_command("seed_catalog")
        call_command("seed_shop_data", sample=True, no_images=True)
        self.assertEqual(Product.objects.count(), 69)

    def test_seed_shop_data_procedural_mode(self):
        from django.core.management import call_command

        call_command("seed_catalog")
        call_command("seed_shop_data", per_category=2, no_images=True)
        # 2 for each of the 34 categories that take listings (10 electronics,
        # 6 vehicles, 5 furniture & appliances, 3 power & solar, 6 fashion,
        # 4 property); group parents ("Vehicles & Auto", "Computers"...) have
        # no products.
        self.assertEqual(Product.objects.count(), 68)

    def test_seeded_shops_can_list_in_the_categories_they_sell(self):
        from django.core.management import call_command

        call_command("seed_catalog")
        for options in ({"sample": True}, {"per_category": 1}):
            with self.subTest(options):
                call_command("seed_shop_data", no_images=True, **options)
                # Without these the seed merchant's listing form offers no
                # category at all.
                for product in Product.objects.select_related("shop", "category"):
                    self.assertIn(
                        product.category,
                        product.shop.categories.all(),
                        f"{product.shop.slug} cannot list {product.category.slug}",
                    )


class ProductModelTest(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="ashuye", password="asdfg1234"
        )
        self.shop = Shop.objects.create(name="Test shop", owner=self.user)
        self.category = Category.objects.create(name="Phones", slug="phones")

    def _product(self, **overrides):
        data = {
            "shop": self.shop,
            "category": self.category,
            "brand": "Apple",
            "name": "iPhone 15 Pro",
            "price": 100,
        }
        data.update(overrides)
        return Product.objects.create(**data)

    def test_default_condition_is_new(self):
        product = self._product()
        self.assertEqual(product.condition, ProductCondition.NEW)
        self.assertEqual(product.condition, "new")

    def test_explicit_condition_is_saved(self):
        used_product = self._product(
            name="Used iPhone", condition=ProductCondition.USED
        )
        refurb_product = self._product(
            name="Refurbished iPhone", condition=ProductCondition.REFURBISHED
        )
        self.assertEqual(used_product.condition, "used")
        self.assertEqual(refurb_product.condition, "refurbished")

    def test_invalid_condition_is_rejected_by_validation(self):
        product = self._product(condition="broken")
        with self.assertRaises(ValidationError) as ctx:
            product.full_clean()
        self.assertIn("condition", ctx.exception.message_dict)

    def test_slug_is_generated_when_blank(self):
        product = self._product()
        self.assertEqual(product.slug, "test-shop-apple-iphone-15-pro")

    def test_reposting_same_item_gets_suffixed_slug(self):
        first = self._product()
        second = self._product()
        self.assertEqual(first.slug, "test-shop-apple-iphone-15-pro")
        self.assertEqual(second.slug, "test-shop-apple-iphone-15-pro-2")

    def test_explicit_slug_is_preserved(self):
        product = self._product(slug="my-custom-slug")
        self.assertEqual(product.slug, "my-custom-slug")

    def test_updating_existing_keeps_its_slug(self):
        first = self._product()
        second = self._product()
        first.name = "Renamed"
        first.save()
        # The name changed but the slug must not be regenerated/leaked.
        self.assertEqual(first.slug, "test-shop-apple-iphone-15-pro")
        self.assertEqual(second.slug, "test-shop-apple-iphone-15-pro-2")

    def test_amharic_only_name_slug_fallback(self):
        # When shop slug is ASCII, Amharic product name produces a valid slug with shop slug base
        first = self._product(brand="ተስፋ", name="ስልክ")
        self.assertEqual(first.slug, "test-shop")

        # When shop slug is empty (e.g. legacy row), fallback to "product" / "product-2"
        amharic_shop = Shop.objects.create(name="ተስፋ ሞባይል", owner=self.user)
        Shop.objects.filter(pk=amharic_shop.pk).update(slug="")
        amharic_shop.refresh_from_db()
        prod1 = Product.objects.create(
            shop=amharic_shop,
            category=self.category,
            brand="ተስፋ",
            name="ሞባይል",
            price="100.00",
        )
        prod2 = Product.objects.create(
            shop=amharic_shop,
            category=self.category,
            brand="ተስፋ",
            name="ሞባይል",
            price="100.00",
        )
        self.assertEqual(prod1.slug, "product")
        self.assertEqual(prod2.slug, "product-2")

    def test_default_status_stock_views_inquiries(self):
        product = self._product()
        self.assertEqual(product.status, "live")
        self.assertEqual(product.stock, 1)
        self.assertEqual(product.views, 0)
        self.assertEqual(product.inquiries, 0)
        self.assertTrue(product.is_available)

    def test_sold_and_draft_status_sets_is_available_false(self):
        draft = self._product(name="Draft Item", status="draft")
        sold = self._product(name="Sold Item", status="sold")
        self.assertFalse(draft.is_available)
        self.assertFalse(sold.is_available)


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class ProductImageModelTest(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="ashuye", password="asdfg1234"
        )
        self.shop = Shop.objects.create(name="Test shop", owner=self.user)
        self.category = Category.objects.create(name="Electronics", slug="electronics")
        self.product = Product.objects.create(
            shop=self.shop,
            category=self.category,
            brand="Hp",
            name="Laptop",
            slug="hp-laptop",
            price=100,
        )

    def _png(self, name="laptop.png", size=(10, 10)):
        buf = io.BytesIO()
        Image.new("RGB", size, (255, 0, 0)).save(buf, format="PNG")
        return SimpleUploadedFile(name, buf.getvalue(), content_type="image/png")

    def test_create_product_image(self):
        image = ProductImage.objects.create(product=self.product, image=self._png())
        self.assertEqual(image.product, self.product)
        self.assertEqual(image.display_order, 0)
        self.assertFalse(image.is_primary)
        self.assertTrue(image.image.name.startswith("product_image/"))

    def test_product_serializer_includes_images(self):
        ProductImage.objects.create(product=self.product, image=self._png())
        serializer = ProductSerializer(self.product)
        data = serializer.data
        self.assertEqual(data["title"], "Hp Laptop")
        self.assertEqual(data["shop_name"], "Test shop")
        self.assertFalse(data["shop_verified"])
        self.assertIsNone(data["shop_logo"])
        self.assertEqual(data["category"]["name"], "Electronics")
        self.assertEqual(data["condition"], "new")
        self.assertEqual(len(data["images"]), 1)
        self.assertEqual(data["images"][0]["display_order"], 0)

    def test_product_serializer_includes_shop_logo(self):
        self.shop.logo = self._png("logo.png")
        self.shop.save()
        data = ProductSerializer(self.product).data
        self.assertTrue(data["shop_logo"].startswith("/media/shops/logos/"))

    def test_primary_image_surfaces_first(self):
        first = ProductImage.objects.create(
            product=self.product, image=self._png("one.png")
        )
        second = ProductImage.objects.create(
            product=self.product,
            image=self._png("two.png"),
            is_primary=True,
        )
        images = ProductSerializer(self.product).data["images"]
        self.assertEqual(images[0]["id"], second.id)
        self.assertTrue(images[0]["is_primary"])
        self.assertEqual(images[1]["id"], first.id)

    def test_serializer_returns_absolute_image_urls(self):
        from rest_framework.test import APIRequestFactory

        ProductImage.objects.create(product=self.product, image=self._png())
        request = APIRequestFactory().get("/api/v1/catalog/products/")
        data = ProductSerializer(self.product, context={"request": request}).data
        self.assertTrue(data["images"][0]["url"].startswith("http://testserver/media/"))

    def test_image_larger_than_5mb_is_rejected(self):
        oversized = SimpleUploadedFile(
            "big.jpg", b"x" * (5 * 1024 * 1024 + 1), content_type="image/jpeg"
        )
        image = ProductImage(product=self.product, image=oversized)
        with self.assertRaises(ValidationError):
            image.clean()

    def test_save_rejects_oversized_upload(self):
        oversized = SimpleUploadedFile(
            "big.jpg", b"x" * (5 * 1024 * 1024 + 1), content_type="image/jpeg"
        )
        with self.assertRaises(ValidationError):
            ProductImage.objects.create(product=self.product, image=oversized)

    def test_non_image_content_is_rejected(self):
        fake = SimpleUploadedFile(
            "fake.jpg", b"not an image", content_type="image/jpeg"
        )
        with self.assertRaises(ValidationError):
            ProductImage.objects.create(product=self.product, image=fake)

    def test_oversized_dimensions_are_rejected(self):
        huge = self._png("huge.png", size=(5000, 5000))
        with self.assertRaises(ValidationError):
            ProductImage.objects.create(product=self.product, image=huge)

    def test_only_one_primary_image_per_product(self):
        ProductImage.objects.create(
            product=self.product, image=self._png("one.png"), is_primary=True
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                ProductImage.objects.create(
                    product=self.product, image=self._png("two.png"), is_primary=True
                )

    def test_deleting_image_removes_file_from_storage(self):
        image = ProductImage.objects.create(product=self.product, image=self._png())
        path = image.image.path
        self.assertTrue(os.path.exists(path))
        image.delete()
        self.assertFalse(os.path.exists(path))

    def test_deleting_product_cascades_and_removes_files(self):
        image = ProductImage.objects.create(product=self.product, image=self._png())
        path = image.image.path
        self.product.delete()
        self.assertEqual(ProductImage.objects.count(), 0)
        self.assertFalse(os.path.exists(path))


class FreshnessRuleTests(SimpleTestCase):
    """The rule itself, pinned to a fixed `now`.

    Both edges are tested from both sides on purpose. A test that only asserts a
    point comfortably inside a band passes for any window at least that wide --
    it would not notice either constant being doubled.
    """

    NOW = timezone.make_aware(datetime(2026, 8, 22, 12, 0))

    def _label(self, **age):
        return get_freshness(self.NOW - timedelta(**age), now=self.NOW)

    def test_well_inside_the_window_is_fresh(self):
        self.assertEqual(self._label(hours=1), Freshness.FRESH)

    def test_exactly_7_days_is_fresh(self):
        self.assertEqual(self._label(days=7), Freshness.FRESH)

    def test_one_second_past_7_days_is_almost_expired(self):
        self.assertEqual(self._label(days=7, seconds=1), Freshness.ALMOST_EXPIRED)

    def test_exactly_14_days_is_almost_expired(self):
        self.assertEqual(self._label(days=14), Freshness.ALMOST_EXPIRED)

    def test_one_second_past_14_days_is_expired(self):
        self.assertEqual(self._label(days=14, seconds=1), Freshness.EXPIRED)

    def test_null_is_expired(self):
        self.assertEqual(get_freshness(None, now=self.NOW), Freshness.EXPIRED)


class FreshnessQuerySetTests(TestCase):
    """The Python rule and the SQL rule have to stay the same rule.

    `Product.freshness` reads one implementation and `with_freshness()` reads
    another. Exercising only one of them would let the two drift apart, which is
    the precise failure `catalog/freshness.py` exists to make impossible.
    """

    AGES = [
        ("never", None),
        ("one-hour", timedelta(hours=1)),
        ("at-7d", FRESH_WINDOW),
        ("past-7d", FRESH_WINDOW + timedelta(seconds=1)),
        ("at-14d", ALMOST_EXPIRED_WINDOW),
        ("past-14d", ALMOST_EXPIRED_WINDOW + timedelta(seconds=1)),
        ("ancient", timedelta(days=90)),
    ]

    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="ashuye", password="asdfg1234"
        )
        self.shop = Shop.objects.create(name="Test shop", owner=self.user)
        self.category = Category.objects.create(name="Phones", slug="phones")
        self.now = timezone.now()

    def _product(self, name, age):
        return Product.objects.create(
            shop=self.shop,
            category=self.category,
            brand="Apple",
            name=name,
            price=100,
            last_confirmed_at=None if age is None else self.now - age,
        )

    def _seed_every_band(self):
        for name, age in self.AGES:
            self._product(name, age)

    def test_sql_rule_matches_python_rule_in_every_band(self):
        self._seed_every_band()
        for product in Product.objects.with_freshness(now=self.now):
            with self.subTest(product=product.name):
                self.assertEqual(
                    product.freshness_label,
                    get_freshness(product.last_confirmed_at, now=self.now),
                )

    def test_fresh_helper_returns_exactly_the_fresh_band(self):
        self._seed_every_band()
        self.assertEqual(
            sorted(Product.objects.fresh(now=self.now).values_list("name", flat=True)),
            ["at-7d", "one-hour"],
        )

    def test_fresh_helper_excludes_never_confirmed(self):
        self._product("never", None)
        self.assertFalse(Product.objects.fresh(now=self.now).exists())

    def test_property_reads_the_shared_rule(self):
        # Sampled well away from the edges: the property reads the live clock,
        # so a boundary value would already have crossed it by assertion time.
        self.assertEqual(self._product("recent", timedelta(hours=1)).freshness, "fresh")
        self.assertEqual(
            self._product("ancient", timedelta(days=90)).freshness, "expired"
        )

    def test_buyer_visible_helper_excludes_expired_and_paused(self):
        self._seed_every_band()
        self.assertEqual(
            sorted(
                Product.objects.buyer_visible(now=self.now).values_list(
                    "name", flat=True
                )
            ),
            ["at-14d", "at-7d", "one-hour", "past-7d"],
        )
        # Verify fresh() on buyer_visible works cleanly
        self.assertEqual(
            sorted(
                Product.objects.buyer_visible(now=self.now)
                .fresh(now=self.now)
                .values_list("name", flat=True)
            ),
            ["at-7d", "one-hour"],
        )
        # Paused / unavailable product is excluded
        p = self._product("paused", timedelta(hours=1))
        p.is_available = False
        p.save()
        self.assertNotIn(
            "paused",
            Product.objects.buyer_visible(now=self.now).values_list("name", flat=True),
        )

    def test_buyer_visible_boundaries_13d_and_15d(self):
        self._product("visible-13d", timedelta(days=13))
        self._product("hidden-15d", timedelta(days=15))
        visible_names = list(
            Product.objects.buyer_visible(now=self.now).values_list("name", flat=True)
        )
        self.assertIn("visible-13d", visible_names)
        self.assertNotIn("hidden-15d", visible_names)
