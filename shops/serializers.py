from rest_framework import serializers

from .models import Shop


class ShopSerializer(serializers.ModelSerializer):
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


class ShopListSerializer(serializers.ModelSerializer):
    class Meta:
        model = Shop
        fields = ["id", "name", "slug", "description", "city", "is_verified"]
