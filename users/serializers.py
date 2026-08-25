from django.contrib.auth.password_validation import validate_password
from django.contrib.auth.tokens import default_token_generator
from django.contrib.auth.validators import UnicodeUsernameValidator
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.utils.encoding import force_str
from django.utils.http import urlsafe_base64_decode
from rest_framework import serializers
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
from rest_framework_simplejwt.tokens import RefreshToken

from shops.models import Shop
from shops.serializers import ShopSerializer

from .models import User
from .validators import looks_like_phone, normalize_email, normalize_phone


def run_password_policy(password, user=None):
    """Apply `AUTH_PASSWORD_VALIDATORS`, re-raised as a DRF error.

    DRF never calls Django's validators on its own, and `create_user()` only
    hashes -- without this the setting is dead and a one-character password is
    accepted.

    `user` matters more than it looks: `UserAttributeSimilarityValidator.validate`
    returns immediately when it is None, so passing an unsaved instance built
    from the submitted data is the only way that validator does anything at
    registration time.
    """
    try:
        validate_password(password, user=user)
    except DjangoValidationError as exc:
        raise serializers.ValidationError(list(exc.messages)) from exc
    return password


class ShopOwnerRegistrationSerializer(serializers.ModelSerializer):
    """Creates a merchant and their first shop as a single unit.

    Identity rules enforced here, all of which the database also enforces:

    - `username` -- unique case-insensitively, and may not take the shape of an
      email or a phone number. Those shapes route elsewhere at login (see
      `users.backends`), so such a username would be unreachable.
    - `phone` -- required, canonicalised to E.164, unique. This is the identifier
      that every Ethiopian merchant actually has.
    - `email` -- optional, but unique when present. Optional because a large
      share of merchants have no email they read; forcing the field yields
      `a@a.com` typed twice, which is a unique constraint over fake data. It is
      the account-recovery channel for those who do supply one.

    `shop_name` is deliberately not unique: `Shop.save()` resolves collisions
    with `-2`/`-3` slugs, so the model is built on the premise that two real
    shops may share a name.

    The fields are declared explicitly rather than left to ModelSerializer so the
    auto-generated `UniqueValidator` is suppressed -- it compares exactly, which
    would let `Sara@x.com` past a stored `sara@x.com` and produce a confusing
    IntegrityError at save instead of a clean 400 here.
    """

    username = serializers.CharField(
        max_length=150, validators=[UnicodeUsernameValidator()]
    )
    email = serializers.EmailField(required=False, allow_blank=True, allow_null=True)
    phone = serializers.CharField(max_length=30)
    shop_name = serializers.CharField(max_length=200)
    city = serializers.CharField(max_length=100)

    class Meta:
        model = User
        fields = ["username", "email", "password", "shop_name", "city", "phone"]
        extra_kwargs = {"password": {"write_only": True}}

    def validate_username(self, value):
        value = value.strip()

        if "@" in value:
            raise serializers.ValidationError(
                "A username cannot contain '@' — that shape is reserved for "
                "logging in by email. Use letters, digits and . + - _ instead."
            )
        if looks_like_phone(value):
            raise serializers.ValidationError(
                "A username cannot be a phone number — that shape is reserved "
                "for logging in by phone."
            )
        if User.objects.filter(username__iexact=value).exists():
            raise serializers.ValidationError(
                "A user with that username already exists."
            )
        return value

    def validate_email(self, value):
        email = normalize_email(value)
        if email and User.objects.filter(email__iexact=email).exists():
            raise serializers.ValidationError("A user with that email already exists.")
        return email

    def validate_phone(self, value):
        try:
            phone = normalize_phone(value)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(list(exc.messages)) from exc
        if phone is None:
            raise serializers.ValidationError("This field may not be blank.")
        if User.objects.filter(phone=phone).exists():
            raise serializers.ValidationError(
                "A user with that phone number already exists."
            )
        return phone

    def validate(self, attrs):
        # Runs here rather than in `validate_password` so the policy sees the
        # rest of the submission -- see `run_password_policy`.
        candidate = User(
            username=attrs.get("username", ""),
            email=attrs.get("email") or "",
        )
        try:
            run_password_policy(attrs.get("password", ""), user=candidate)
        except serializers.ValidationError as exc:
            raise serializers.ValidationError({"password": exc.detail}) from exc
        return attrs

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


class MultiIdentifierTokenObtainPairSerializer(TokenObtainPairSerializer):
    """Login by username, email or phone.

    `username_field` is a documented hook on simplejwt's serializer: it names the
    input field and is the kwarg handed to `authenticate()`. Setting it to
    `identifier` gives an honest request shape and lets
    `users.backends.MultiIdentifierBackend` decide which column that value means.
    """

    username_field = "identifier"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # `username` is the field the Next.js login form posts today. Accepting
        # both means this ships without a coordinated frontend release; the alias
        # can be dropped once the form posts `identifier`.
        self.fields[self.username_field].required = False
        self.fields["username"] = serializers.CharField(write_only=True, required=False)

    def validate(self, attrs):
        identifier = attrs.get(self.username_field) or attrs.pop("username", None)
        if not identifier:
            raise serializers.ValidationError(
                {self.username_field: "This field is required."}
            )
        attrs[self.username_field] = identifier
        attrs.pop("username", None)
        return super().validate(attrs)


class LogoutSerializer(serializers.Serializer):
    refresh = serializers.CharField(write_only=True)

    def validate_refresh(self, value):
        try:
            return RefreshToken(value)
        except TokenError as exc:
            raise serializers.ValidationError(
                "That refresh token is invalid or has already expired."
            ) from exc


class PasswordChangeSerializer(serializers.Serializer):
    current_password = serializers.CharField(write_only=True)
    new_password = serializers.CharField(write_only=True)

    def validate_current_password(self, value):
        """Re-authenticate before allowing the change.

        Without this a stolen access token is enough to take an account over
        permanently; with it the attacker also needs the password they were
        trying to replace.
        """
        user = self.context["request"].user
        if not user.check_password(value):
            raise serializers.ValidationError("Your current password is incorrect.")
        return value

    def validate_new_password(self, value):
        return run_password_policy(value, user=self.context["request"].user)

    def validate(self, attrs):
        if attrs["current_password"] == attrs["new_password"]:
            raise serializers.ValidationError(
                {"new_password": "The new password must differ from the current one."}
            )
        return attrs


class PasswordResetRequestSerializer(serializers.Serializer):
    email = serializers.EmailField()

    def validate_email(self, value):
        # Normalise only. Whether the address belongs to an account is decided by
        # the view, which must answer identically either way.
        return normalize_email(value)


class PasswordResetConfirmSerializer(serializers.Serializer):
    """Validates the emailed uid/token pair and the proposed password.

    One subtlety worth knowing: Django's `default_token_generator` hashes
    `user.password` **and** `user.last_login`. Because `UPDATE_LAST_LOGIN` is on,
    a successful login between requesting a reset and following the link
    invalidates the link. That is Django's intended behaviour -- and desirable,
    since someone who can still log in does not need the reset -- but it is
    surprising if you have not seen it before.
    """

    uid = serializers.CharField()
    token = serializers.CharField()
    new_password = serializers.CharField(write_only=True)

    default_error_messages = {
        "invalid_link": "This password reset link is invalid or has expired. "
        "Please request a new one.",
    }

    def validate(self, attrs):
        try:
            pk = force_str(urlsafe_base64_decode(attrs["uid"]))
            user = User.objects.get(pk=pk, is_active=True)
        except (TypeError, ValueError, OverflowError, User.DoesNotExist) as exc:
            raise serializers.ValidationError(
                self.error_messages["invalid_link"]
            ) from exc

        if not default_token_generator.check_token(user, attrs["token"]):
            raise serializers.ValidationError(self.error_messages["invalid_link"])

        try:
            run_password_policy(attrs["new_password"], user=user)
        except serializers.ValidationError as exc:
            raise serializers.ValidationError({"new_password": exc.detail}) from exc

        attrs["user"] = user
        return attrs
