from rest_framework import serializers

from .models import Category, Product


class CategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = Category
        fields = ["id", "name", "slug", "parent", "display_order"]


class ProductSerializer(serializers.ModelSerializer):
    title = serializers.SerializerMethodField()
    shop_name = serializers.CharField(source="shop.name", read_only=True)
    shop_verified = serializers.BooleanField(source="shop.is_verified", read_only=True)
    category = CategorySerializer(read_only=True)

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
            "image",
            "last_confirmed_at",
            "confirmation_status",
            "slug",
            "specs",
        ]

    def get_title(self, obj):
        return f"{obj.brand} {obj.name}"
