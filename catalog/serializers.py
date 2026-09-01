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
    # Annotated by `catalog.services.SearchProduct`, so they are absent on the
    # plain list/detail routes. Read-only means DRF raises SkipField rather than
    # AttributeError there, and the keys are simply omitted.
    verified_score = serializers.IntegerField(read_only=True)
    typo_score = serializers.FloatField(read_only=True)

    class Meta:
        model = Product
        # One trust signal for buyers, plus the raw timestamp behind it.
        #
        # `freshness` used to ship here too, and the pair could disagree in a
        # single response: a product confirmed ten days ago serialised as
        # {"confirmation_status": "fresh", "freshness": "expired"}, because the
        # two read different clocks -- 30 days for the prompting workflow, 48h/7d
        # for the derived tier. A client had to pick one, and whichever it picked
        # the other was wrong on screen.
        #
        # `confirmation_status` won because it is the one the loop actually
        # maintains and the only one that can say `unconfirmed`. The derived tier
        # is still computed and still load-bearing -- `ProductViewSet` ranks on
        # it via `freshness_rank` -- it just is not a label buyers are shown.
        # `ShopProductSerializer` keeps it: merchants benefit from the finer
        # signal, and there is no second label there to contradict.
        #
        # Render age from `last_confirmed_at` ("confirmed 3 days ago") rather
        # than from a tier name. It cannot drift and needs no threshold pinned
        # before real shops have told us what the thresholds should be.
        fields = [
            "id",
            "title",
            "description",
            "price",
            "currency",
            "condition",
            "shop_name",
            "shop_verified",
            "category",
            "images",
            "last_confirmed_at",
            "confirmation_status",
            "verified_score",
            "typo_score",
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
