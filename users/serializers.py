from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from rest_framework import serializers

from shops.models import Shop
from shops.serializers import ShopSerializer

from .models import User


class ShopOwnerRegistrationSerializer(serializers.ModelSerializer):
    """Creates a merchant and their first shop as a single unit.

    `username` is the login identifier -- `users.User` inherits
    `USERNAME_FIELD = "username"` from `AbstractUser` -- and is the only field
    here the database enforces as unique. ModelSerializer derives that
    uniqueness check from the model field, so there is no hand-written
    validator for it; a duplicate already returns a clear, specific 400.

    Two things are deliberately *not* unique:

    - `shop_name`. `Shop.save()` resolves name collisions with `-2`/`-3` slugs,
      so the model is built on the premise that two real shops may share a name.
    - `email`. It carries no DB constraint, so validating it in the serializer
      would be a check-then-act race enforcing a rule the schema does not have.
    """

    shop_name = serializers.CharField(max_length=200)
    city = serializers.CharField(max_length=100)
    phone = serializers.CharField(max_length=30)

    class Meta:
        model = User
        fields = ["username", "email", "password", "shop_name", "city", "phone"]
        extra_kwargs = {"password": {"write_only": True}}

    def validate_password(self, value):
        """Run the project's configured password policy.

        DRF never calls Django's validators and `create_user()` only hashes, so
        without this `AUTH_PASSWORD_VALIDATORS` is a dead setting and a
        one-character password is accepted.
        """
        try:
            validate_password(value)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(list(exc.messages)) from exc
        return value

    def create(self, validated_data):
        shop_name = validated_data.pop("shop_name")
        city = validated_data.pop("city")
        # Left in validated_data as well: the merchant's own number and their
        # shop's published contact number are the same at registration time.
        phone = validated_data["phone"]

        # One unit. A User with no Shop is the failure that matters -- the
        # account could log in with nothing to manage, and re-registering would
        # collide on the username.
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
    """The /me/ shape.

    `shops` is a list because `Shop.owner` is a ForeignKey: registration creates
    exactly one, but the model permits more and `seed_shop_data` relies on that.
    A singular key here would misreport the schema.
    """

    shops = ShopSerializer(many=True, read_only=True)

    class Meta:
        model = User
        fields = ["id", "username", "email", "phone", "shops"]
