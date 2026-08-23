from rest_framework import serializers

from .models import Category, Product, ProductImage


class CategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = Category
        fields = ["id", "name", "slug", "parent", "display_order"]


class ProductImageSerializer(serializers.ModelSerializer):
    url = serializers.SerializerMethodField()

    class Meta:
        model = ProductImage
        fields = ["id", "url", "is_primary", "display_order"]

    def get_url(self, obj):
        request = self.context.get("request")
        if request:
            return request.build_absolute_uri(obj.image.url)
        return obj.image.url


class ProductSerializer(serializers.ModelSerializer):
    title = serializers.SerializerMethodField()
    shop_name = serializers.CharField(source="shop.name", read_only=True)
    shop_verified = serializers.BooleanField(source="shop.is_verified", read_only=True)
    category = CategorySerializer(read_only=True)
    images = serializers.SerializerMethodField()
    # Derived at read time from `last_confirmed_at`, never stored. Reads the
    # model property, which costs nothing extra: the column is already loaded.
    freshness = serializers.ReadOnlyField()

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
            "confirmation_status",
            "freshness",
            "slug",
            "specs",
        ]

    def get_title(self, obj):
        return f"{obj.brand} {obj.name}"

    def get_images(self, obj):
        # Ordering belongs to the viewset's Prefetch — re-sorting here would
        # throw the prefetched rows away and re-query per product.
        return ProductImageSerializer(
            obj.product_image.all(), many=True, context=self.context
        ).data
