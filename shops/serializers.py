from rest_framework import serializers

from catalog.models import Category, Product

from .models import Shop


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
            "telegram_chat_id",
            "is_verified",
            "owner",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["slug", "is_verified", "owner", "created_at", "updated_at"]


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
            "is_verified",
            "created_at",
            "updated_at",
        ]


class ShopListSerializer(serializers.ModelSerializer):
    class Meta:
        model = Shop
        fields = ["id", "name", "slug", "description", "city", "is_verified"]


class ShopProductSerializer(serializers.ModelSerializer):
    """The merchant's write shape for their own products.

    Deliberately narrower than `catalog.ShopProductSerializer`'s buyer-facing
    cousin on the write side: the confirmation clock is not a merchant-editable
    field. See `read_only_fields`.
    """

    # A slug rather than a pk: the dashboard's category select works in slugs,
    # and DRF turns an unknown or missing one into a 400 field error instead of
    # the 404 a manual get_object_or_404 lookup would raise.
    category = serializers.SlugRelatedField(
        slug_field="slug", queryset=Category.objects.all()
    )

    class Meta:
        model = Product
        fields = [
            "id",
            "name",
            "brand",
            "description",
            "price",
            "currency",
            "shop",
            "category",
            "is_available",
            "last_confirmed_at",
            "confirmation_status",
            "slug",
            "specs",
        ]
        # `last_confirmed_at` and `confirmation_status` are readable but never
        # writable. `catalog.services.apply_confirmation` is the single writer of
        # the confirmation clock; exposing these would let a merchant PATCH their
        # own listings permanently fresh and hollow out the whole signal.
        # `shop` comes from the URL, and `slug` is derived in `Product.save()`.
        read_only_fields = [
            "shop",
            "slug",
            "last_confirmed_at",
            "confirmation_status",
        ]
