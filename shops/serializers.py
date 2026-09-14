from rest_framework import serializers

from catalog.models import Category, Product, ProductImage

from .models import Shop, VerificationRequest


class ShopSerializer(serializers.ModelSerializer):
    """Owner/staff view, and the write shape for create and update."""

    class Meta:
        model = Shop
        fields = [
            "id",
            "name",
            "slug",
            "description",
            "phone",
            "city",
            "latitude",
            "longitude",
            "telegram_chat_id",
            "is_verified",
            "owner",
            "created_at",
            "updated_at",
        ]
        # `telegram_chat_id` is readable here so an owner can see whether their
        # shop is linked, but it is never writable over the API. It is the bot's
        # routing address and it carries no unique constraint, so a writable
        # field lets any merchant PATCH another shop's chat id onto their own --
        # `bot.selectors.shops_for_chat` then returns both shops and the
        # victim's Telegram session reaches the intruder's catalogue. The only
        # way to bind a chat is `ShopLinkCode`: one-time, short-lived, and spent
        # under a row lock. Same reasoning as `ShopProductSerializer`, which
        # withholds the confirmation clock.
        read_only_fields = [
            "slug",
            "telegram_chat_id",
            "is_verified",
            "owner",
            "created_at",
            "updated_at",
        ]


class ShopPublicSerializer(serializers.ModelSerializer):
    """Shop detail as anyone else sees it.

    `telegram_chat_id` is the bot's routing address for the merchant and `owner`
    is an internal user id — neither is storefront data, and the detail endpoint
    is readable anonymously. `phone` stays: it is the shop's published contact
    number and the storefront is meant to show it.
    """

    class Meta:
        model = Shop
        fields = [
            "id",
            "name",
            "slug",
            "description",
            "phone",
            "city",
            "latitude",
            "longitude",
            "is_verified",
            "created_at",
            "updated_at",
        ]


class ShopListSerializer(serializers.ModelSerializer):
    active_listings_count = serializers.IntegerField(read_only=True, default=0)

    class Meta:
        model = Shop
        fields = [
            "id",
            "name",
            "slug",
            "description",
            "phone",
            "city",
            "latitude",
            "longitude",
            "is_verified",
            "active_listings_count",
            "created_at",
        ]


class ShopProductSerializer(serializers.ModelSerializer):
    """The merchant's write shape for their own products.

    Deliberately narrower than `catalog.ProductSerializer`, the buyer-facing
    view of the same model: the confirmation clock is not a merchant-editable
    field. See `read_only_fields`.
    """

    # A slug rather than a pk: the dashboard's category select works in slugs,
    # and DRF turns an unknown or missing one into a 400 field error instead of
    # the 404 a manual get_object_or_404 lookup would raise.
    category = serializers.SlugRelatedField(
        slug_field="slug", queryset=Category.objects.all()
    )
    # The dashboard's badge. Reads `Product.freshness`, which delegates to
    # `catalog.freshness.get_freshness` -- the same function the buyer-facing
    # serializer and the SQL annotation both go through. Nothing recomputes the
    # window here, and nothing should recompute it in the client either.
    freshness = serializers.ReadOnlyField()
    shop_slug = serializers.CharField(source="shop.slug", read_only=True)

    class Meta:
        model = Product
        fields = [
            "id",
            "name",
            "brand",
            "description",
            "price",
            "currency",
            "condition",
            "status",
            "stock",
            "views",
            "inquiries",
            "shop",
            "shop_slug",
            "category",
            "is_available",
            "last_confirmed_at",
            "confirmation_status",
            "freshness",
            "slug",
            "specs",
        ]
        # `last_confirmed_at` and `confirmation_status` are readable but never
        # writable. `catalog.services.apply_confirmation` is the single writer of
        # the confirmation clock; exposing these would let a merchant PATCH their
        # own listings permanently fresh and hollow out the whole signal.
        # `shop` comes from the URL, and `slug` is derived in `Product.save()`.
        # `views` and `inquiries` are metric counters updated through analytics.
        read_only_fields = [
            "shop",
            "slug",
            "last_confirmed_at",
            "confirmation_status",
            "views",
            "inquiries",
        ]


class ShopProductImageSerializer(serializers.ModelSerializer):
    """Images for one of the merchant's own products.

    `product` is read-only for the same reason `shop` is on the product
    serializer: it comes from the URL, which has already been ownership-checked.
    Accepting it in the body would let a merchant attach an image to someone
    else's listing.
    """

    class Meta:
        model = ProductImage
        fields = ["id", "image", "display_order", "is_primary", "product"]
        read_only_fields = ["product"]

    def validate_is_primary(self, value):
        """Surface the partial unique constraint as a field error, not a 500.

        `ProductImage` carries a UniqueConstraint on (product) where
        is_primary=True. Without this check the second primary image raises
        IntegrityError out of the database and DRF returns a 500.
        """
        if not value:
            return value
        product = self.context.get("product")
        if product is None:
            return value
        clash = ProductImage.objects.filter(product=product, is_primary=True)
        if self.instance is not None:
            clash = clash.exclude(pk=self.instance.pk)
        if clash.exists():
            raise serializers.ValidationError(
                "This product already has a primary image."
            )
        return value


class VerificationRequestSerializer(serializers.ModelSerializer):
    is_verified = serializers.BooleanField(source="shop.is_verified", read_only=True)

    class Meta:
        model = VerificationRequest
        fields = [
            "id",
            "shop",
            "is_verified",
            "submitted_at",
            "reviewed_at",
            "reviewer",
            "state",
            "rejection_reason",
            "supporting_detail",
        ]
        read_only_fields = [
            "shop",
            "is_verified",
            "submitted_at",
            "reviewed_at",
            "reviewer",
            "state",
            "rejection_reason",
        ]
        extra_kwargs = {
            "supporting_detail": {
                "required": True,
                "allow_blank": False,
                "error_messages": {
                    "blank": "Please provide supporting business details or documents.",
                    "required": "Please provide supporting business details or documents.",
                },
            }
        }
