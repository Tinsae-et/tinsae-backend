import io
import os
import tempfile

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from PIL import Image

from catalog.services import create_pending_request, resolve_request, expire_confirmation_requests
from catalog.models import Category, Product, ProductImage, ConfirmationRequest
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

class ConfirmationRequestModelTest(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="usename", password="password"
        )
        self.shop = Shop.objects.create(name="Test shop", owner=self.user)
        self.category = Category.objects.create(name="Phones", slug="phones")
        self.product = Product.objects.create(
            shop=self.shop,
            category=self.category,
            brand="Samsung",
            name="Galaxy-s25",
            slug="samsung-galaxy-s25",
            price=123000,
        )
    
    def test_create_pending_confirmation_request(self):
        confirmation_request = create_pending_request(self.product, self.shop)
        self.assertEqual(confirmation_request.id, 1)

    def test_resolve_request_status(self):
        confirmation_request = create_pending_request(self.product, self.shop)
        with self.assertRaisesMessage(ValidationError, "Can not resolve a request that is already pending"):
            resolve_request(confirmation_request, "pending")

        status = "not confirmed"
        with self.assertRaisesMessage(ValidationError, f"Invalid resolution: {status}"):
            resolve_request(confirmation_request, status)

        resolve_request(confirmation_request, "confirmed")
        self.assertEqual(confirmation_request.status, "confirmed")
        self.assertTrue(confirmation_request.status != None)
