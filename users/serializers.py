from django.db import transaction
from rest_framework import serializers

from shops.models import Shop
from shops.serializers import ShopSerializer

from .models import User


class ShopOwnerRegistrationSerializer(serializers.ModelSerializer):
    shop_name = serializers.CharField(max_length=100)
    city = serializers.CharField(max_length=100)
    phone = serializers.CharField(max_length=30)

    class Meta:
        model = User
        fields = ["username", "email", "password", "shop_name", "city", "phone"]
        extra_kwargs = {"password": {"write_only": True}}

    def validate_email(self, value):
        if User.objects.filter(email=value).exists():
            raise serializers.ValidationError(
                "An account with this email already existed."
            )
        return value

    def validate_shop_name(self, value):
        if Shop.objects.filter(name=value).exists():
            raise serializers.ValidationError("Shop with this name already existed.")
        return value

    def create(self, validated_data):
        shop_name = validated_data.pop("shop_name")
        city = validated_data.pop("city")
        phone = validated_data.get("phone")

        with transaction.atomic():
            user = User.objects.create_user(**validated_data)
            Shop.objects.create(
                name=shop_name,
                phone=phone,
                city=city,
                owner=user,
            )
        return user


class UserSerializer(serializers.ModelSerializer):
    shops = ShopSerializer(many=True, read_only=True)

    class Meta:
        model = User
        fields = ["username", "email", "shops"]
