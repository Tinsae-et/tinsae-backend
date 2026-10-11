from rest_framework import serializers

from .models import Category, Product, ProductImage
from .specs import sells_or_rents


class CategorySerializer(serializers.ModelSerializer):
    # Whether listings here have a spec form and spec filters: true for
    # "Cars", false for the "Vehicles & Auto" parent that only groups them.
    has_specs = serializers.SerializerMethodField()

    class Meta:
        model = Category
        fields = [
            "id",
            "name",
            "slug",
            "parent",
            "display_order",
            "has_specs",
            "icon",
            "tagline",
        ]

    def get_has_specs(self, obj):
        return obj.product_type_id is not None


class CategoryNavSerializer(CategorySerializer):
    """A category as menus need it, with what its listings are.

    Only the category list uses it, where product types and children are
    prefetched; on every product's nested category it would cost a query each.
    """

    # Listings here are for sale or for rent (property): pages offer a
    # "For rent | For sale" switch. A parent is when all its children are.
    sale_or_rent = serializers.SerializerMethodField()

    class Meta(CategorySerializer.Meta):
        fields = [*CategorySerializer.Meta.fields, "sale_or_rent"]

    def get_sale_or_rent(self, obj):
        if obj.product_type_id is not None:
            return sells_or_rents(obj.product_type.key)
        children = obj.children.all()
        return bool(children) and all(
            child.product_type_id is not None and sells_or_rents(child.product_type.key)
            for child in children
        )


class CategoryTreeSerializer(CategoryNavSerializer):
    """A category with its subcategories, for navigation.

    Only the category list uses it: nesting `children` into every product's
    category would cost a query per product.
    """

    children = CategoryNavSerializer(many=True, read_only=True)

    class Meta(CategoryNavSerializer.Meta):
        fields = [*CategoryNavSerializer.Meta.fields, "children"]


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
    shop_slug = serializers.CharField(source="shop.slug", read_only=True)
    shop_verified = serializers.BooleanField(source="shop.is_verified", read_only=True)
    category = CategorySerializer(read_only=True)
    images = serializers.SerializerMethodField()
    # `typo_score`, `freshness_score` and `rank_score` are deliberately not
    # serialised. They are internals of `catalog.services.apply_search` whose
    # values will move every time the weights are retuned against query logs,
    # and shipping them would turn each retune into a breaking payload change.
    # `shop_verified` already tells a client everything the verified boost
    # encodes. Rank is expressed as the order of the list, which is the part
    # clients can actually use.

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
            "shop_slug",
            "shop_verified",
            "category",
            "images",
            "last_confirmed_at",
            "confirmation_status",
            "slug",
            "specs",
        ]

    def get_title(self, obj):
        # An unbranded listing is just its name, without a leading space.
        return f"{obj.brand} {obj.name}".strip()

    def get_images(self, obj):
        # Ordering belongs to the viewset's Prefetch — re-sorting here would
        # throw the prefetched rows away and re-query per product.
        return ProductImageSerializer(
            obj.product_image.all(), many=True, context=self.context
        ).data
