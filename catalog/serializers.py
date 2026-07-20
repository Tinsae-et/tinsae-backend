from rest_framework import serializers

from .models import Category, Product, ProductImage


class CategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = Category
        fields = ["id", "name", "slug", "parent", "display_order"]


class ProductImageSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProductImage
        fields = ["id", "image", "is_primary", "display_order"]


class ProductSerializer(serializers.ModelSerializer):
    title = serializers.SerializerMethodField()
    shop_name = serializers.CharField(source="shop.name", read_only=True)
    shop_verified = serializers.BooleanField(source="shop.is_verified", read_only=True)
    category = CategorySerializer(read_only=True)
    images = serializers.SerializerMethodField()

    class Meta:
        model = Product
        fields = [
            "id",
            "title",
            "description",
            "price",
            "currency",
            "shop_name",
            "shop_verified",
            "category",
            "images",
            "last_confirmed_at",
            "slug",
            "specs",
        ]

    def get_title(self, obj):
        return f"{obj.brand} {obj.name}"

    def get_images(self, obj):
        qs = obj.product_image.all().order_by("-is_primary", "display_order")
        return ProductImageSerializer(qs, many=True, context=self.context).data
