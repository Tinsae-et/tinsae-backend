from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

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


class ProductImageModelTest(TestCase):
    def test_create_product_image(self):
        User = get_user_model()
        self.user = User.objects.create_user(username="ashuye", password="asdfg1234")
        self.shop = Shop.objects.create(name="Test shop", owner=self.user)
        self.category = Category.objects.create(name="Electronics", slug="electronsics")
        self.product = Product.objects.create(
            shop=self.shop,
            category=self.category,
            brand="Hp",
            name="Laptop",
            slug="dell-laptop",
            price=100,
        )
        image = SimpleUploadedFile(
            "laptop.jpg", b"fake image content", content_type="image/jpeg"
        )
        self.product_image = ProductImage.objects.create(
            product=self.product, image=image
        )

        def test_create_product_image(self):
            self.assertEqual(self.product_image.product, self.product)
            self.assertEqual(self.product_image.display_order, 0)
            self.assertEqual(self.product_image.is_primary)
            self.assertTrue(self.product_image.image.name.startswith("product_image/"))

        def test_product_serializer(self):
            serializer = ProductSerializer(self.product)
            data = serializer.data
            self.assertEqual(data["title"], "Hp Laptop")
            self.assertEqual(data["shop_name"], "Test Shop")
            self.assertTrue(data["shop_verified"])
            self.assertEqual(data["catagory"]["name"], "Electronics")

            self.assertEqual(len(data["images"]), 1)
            self.assertTrue(data["images"][0]["is_primary"])
            self.assertEqual(data["images"][0]["display_order"], 0)
