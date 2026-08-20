import io
import os
import tempfile
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone
from PIL import Image

from catalog.freshness import get_freshness
from catalog.models import Category, Product, ProductImage
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
        call_command("seed_catalog")
        self.assertEqual(Category.objects.count(), 2)


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
        self.assertEqual(data["category"]["name"], "Electronics")
        self.assertEqual(len(data["images"]), 1)
        self.assertEqual(data["images"][0]["display_order"], 0)

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


class FreshnessTests(SimpleTestCase):
    def test_exactly_48_hours_is_fresh(self):
        last_confirmed_at = timezone.now() - timedelta(hours=48) + timedelta(seconds=1)
        self.assertEqual(get_freshness(last_confirmed_at), "fresh")

    def test_exactly_7_days_is_almost_expired(self):
        last_confirmed_at = timezone.now() - timedelta(days=7) + timedelta(seconds=1)
        self.assertEqual(get_freshness(last_confirmed_at), "almost_expired")

    def test_null_is_expired(self):
        self.assertEqual(get_freshness(None), "expired")
