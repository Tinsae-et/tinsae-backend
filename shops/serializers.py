from rest_framework import serializers

from .models import Shop
from catalog.models import Product

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
            "last_confirmed_at",
            "confirmation_status",
            "slug",
            "specs",
        ]

        read_only_fields = ["shop", "category"]