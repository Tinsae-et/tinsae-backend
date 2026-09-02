from decimal import Decimal

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
from .services import derive_username
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


class RegistrationSerializer(serializers.ModelSerializer):
    """Creates a buyer, or a merchant and their first shop, in one request.

    One endpoint, two outcomes, chosen by `role`. A buyer is simply a `User` with
    no `Shop`: nothing stores a role, and `shops == []` is the whole of what
    "buyer" means. A role column would be a second answer to the same question,
    free to disagree with the first.

    Identity rules enforced here, all of which the database also enforces:

    - `email` -- required, unique case-insensitively. Required because it is the
      only channel that can deliver a verification code today (see `request_otp`)
      and the only account-recovery route. The column stays nullable: signups
      predating this rule have none.
    - `phone` -- canonicalised to E.164, unique. Required for a merchant, because
      `Shop.phone` is the number buyers tap to call and a shop nobody can reach
      is a dead listing. Optional for a buyer, who has nothing to be called about.
    - `username` -- optional, derived from the email when omitted. When supplied
      it may not take the shape of an email or a phone number: those shapes route
      elsewhere at login (see `users.backends`), so such a username would be
      unreachable.

    `shop_name` is deliberately not unique: `Shop.save()` resolves collisions
    with `-2`/`-3` slugs, so the model is built on the premise that two real
    shops may share a name.

    The fields are declared explicitly rather than left to ModelSerializer so the
    auto-generated `UniqueValidator` is suppressed -- it compares exactly, which
    would let `Sara@x.com` past a stored `sara@x.com` and produce a confusing
    IntegrityError at save instead of a clean 400 here.
    """

    ROLE_BUYER = "buyer"
    ROLE_OWNER = "owner"

    role = serializers.ChoiceField(
        choices=[ROLE_BUYER, ROLE_OWNER], required=False, write_only=True
    )
    username = serializers.CharField(
        max_length=150,
        required=False,
        allow_blank=True,
        validators=[UnicodeUsernameValidator()],
    )
    email = serializers.EmailField()
    # Collected at signup so an account has a name from the moment it exists.
    # Optional at the field level for the same back-compat reason as `role`: the
    # signup form has to ship the field before we can require it. Tighten to
    # required once the frontend deploy is confirmed live -- until then a blank
    # one falls back to the username wherever a name is rendered.
    full_name = serializers.CharField(max_length=150, required=False, allow_blank=True)
    phone = serializers.CharField(max_length=30, required=False, allow_blank=True)
    shop_name = serializers.CharField(max_length=200, required=False, allow_blank=True)
    city = serializers.CharField(max_length=100, required=False, allow_blank=True)
    latitude = serializers.DecimalField(
        max_digits=9,
        decimal_places=6,
        required=False,
        allow_null=True,
        min_value=Decimal("-90.0"),
        max_value=Decimal("90.0"),
    )
    longitude = serializers.DecimalField(
        max_digits=9,
        decimal_places=6,
        required=False,
        allow_null=True,
        min_value=Decimal("-180.0"),
        max_value=Decimal("180.0"),
    )

    class Meta:
        model = User
        fields = [
            "username",
            "full_name",
            "email",
            "password",
            "role",
            "shop_name",
            "city",
            "phone",
            "latitude",
            "longitude",
        ]
        extra_kwargs = {"password": {"write_only": True}}

    def validate_username(self, value):
        value = (value or "").strip()
        # Blank is not "no username" -- it is "derive one in create()".
        if not value:
            return ""

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
        # Optional at field level; `validate()` decides when a role requires it.
        if not str(value or "").strip():
            return None
        try:
            phone = normalize_phone(value)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(list(exc.messages)) from exc
        if phone and User.objects.filter(phone=phone).exists():
            raise serializers.ValidationError(
                "A user with that phone number already exists."
            )
        return phone

    def validate(self, attrs):
        # `role` is optional only for backward compatibility -- the same move as
        # the `username`/`identifier` alias in
        # MultiIdentifierTokenObtainPairSerializer. Today's signup form posts no
        # `role`, so `shop_name` stands in until it does, then this goes away.
        #
        # An explicit `role` always wins. The fallback reads *any* shop field,
        # not `shop_name` alone: a payload carrying `city` but no `shop_name` is
        # a botched owner signup, and inferring "buyer" from it would answer 201
        # to a merchant who believes they just registered a shop and has none.
        # A real buyer submits neither field.
        role = attrs.get("role") or (
            self.ROLE_OWNER
            if (attrs.get("shop_name") or attrs.get("city"))
            else self.ROLE_BUYER
        )
        attrs["role"] = role

        # Collected rather than raised one at a time: a merchant missing two
        # fields should see both, not discover the second after fixing the first.
        errors = {}
        if role == self.ROLE_OWNER:
            if not attrs.get("shop_name"):
                errors["shop_name"] = "This field is required when registering a shop."
            if not attrs.get("city"):
                errors["city"] = "This field is required when registering a shop."
            if not attrs.get("phone"):
                errors["phone"] = "A shop needs a phone number buyers can call."
        if errors:
            raise serializers.ValidationError(errors)

        # Runs here rather than in `validate_password` so the policy sees the
        # rest of the submission -- see `run_password_policy`.
        candidate = User(
            username=attrs.get("username") or "",
            email=attrs.get("email") or "",
        )
        try:
            run_password_policy(attrs.get("password", ""), user=candidate)
        except serializers.ValidationError as exc:
            raise serializers.ValidationError({"password": exc.detail}) from exc
        return attrs

    def create(self, validated_data):
        role = validated_data.pop("role")
        shop_name = validated_data.pop("shop_name", "")
        city = validated_data.pop("city", "")
        latitude = validated_data.pop("latitude", None)
        longitude = validated_data.pop("longitude", None)
        # Left in validated_data as well: the merchant's own number and their
        # shop's published contact number are the same at registration time.
        phone = validated_data.get("phone")

        if not validated_data.get("username"):
            validated_data["username"] = derive_username(validated_data["email"])

        # Still one unit for an owner: a User with no Shop is the failure that
        # matters -- the account could log in with nothing to manage. A buyer has
        # no second half to lose, so `shops: []` is a finished registration and
        # not a half-written one.
        with transaction.atomic():
            user = User.objects.create_user(**validated_data)
            if role == self.ROLE_OWNER:
                Shop.objects.create(
                    name=shop_name,
                    phone=phone,
                    city=city,
                    latitude=latitude,
                    longitude=longitude,
                    owner=user,
                )
        return user


# The old name, kept so existing imports keep working. Safe to delete once the
# test suite is green and nothing outside this module references it.
ShopOwnerRegistrationSerializer = RegistrationSerializer


AVATAR_MAX_BYTES = 2 * 1024 * 1024


class UserSerializer(serializers.ModelSerializer):
    """The /me/ shape, readable and writable.

    `email` and `phone` are read-only here on purpose. Both are login
    identifiers, `email` is the only channel that can deliver an OTP and the
    only password-reset route, and neither can be re-verified today. A writable
    `email` would let any authenticated caller take an address they do not own
    -- permanently, since it is unique -- and redirect that account's password
    resets on the way. They become writable through a verified change flow, not
    through a PATCH: see `docs/otp-verified-password-signup.md`.

    `shops` is a list because `Shop.owner` is a ForeignKey: registration creates
    exactly one, but the model permits more and `seed_shop_data` relies on that.
    A singular key here would misreport the schema.
    """

    shops = ShopSerializer(many=True, read_only=True)
    avatar = serializers.ImageField(required=False, allow_null=True)
    phone_verified = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            "id",
            "username",
            "full_name",
            "email",
            "phone",
            "phone_verified",
            "preferred_area",
            "telegram_username",
            "telegram_chat_id",
            "avatar",
            "date_joined",
            "shops",
        ]
        read_only_fields = [
            "id",
            "username",
            "email",
            "phone",
            "telegram_chat_id",
            "date_joined",
        ]

    def get_phone_verified(self, obj):
        """Always False, and a method rather than a column on purpose.

        Nothing can set it True: `request_otp` refuses non-email destinations
        and there is no SMS provider. A stored field would be a column no code
        writes. This keeps the key the settings page expects, and becomes a real
        field the day SMS lands.
        """
        return False

    def validate_avatar(self, value):
        """Enforce the 2 MB / JPG-PNG limits the settings page advertises.

        `ImageField` enforces neither -- it only confirms Pillow can open the
        file, so a 40 MB TIFF renamed to .jpg passes it untouched.
        """
        if value is None:
            return None
        if value.size > AVATAR_MAX_BYTES:
            raise serializers.ValidationError("Keep the image under 2 MB.")
        # DRF's ImageField runs the upload through Django's, which attaches the
        # decoded image -- so this is Pillow's verdict on the bytes, not the
        # filename's claim.
        image_format = getattr(getattr(value, "image", None), "format", "") or ""
        if image_format.upper() not in {"JPEG", "PNG"}:
            raise serializers.ValidationError("Upload a JPG or PNG image.")
        return value

    def update(self, instance, validated_data):
        # Nothing else deletes avatars. Without this every re-upload leaves the
        # previous file behind and the media directory grows by one file per
        # edit, forever.
        old_name = instance.avatar.name if instance.avatar else ""
        user = super().update(instance, validated_data)
        if old_name and user.avatar.name != old_name:
            user.avatar.storage.delete(old_name)
        return user


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


class OTPRequestSerializer(serializers.Serializer):
    destination = serializers.CharField(required=False)
    email = serializers.EmailField(required=False, allow_blank=True)
    phone = serializers.CharField(required=False, allow_blank=True)
    mode = serializers.ChoiceField(choices=["signin", "signup"], default="signup")

    def validate(self, attrs):
        dest = attrs.get("destination") or attrs.get("email") or attrs.get("phone")
        if not dest:
            raise serializers.ValidationError(
                {"destination": "An email address or phone number is required."}
            )
        attrs["destination"] = str(dest).strip()
        return attrs


class OTPVerifySerializer(serializers.Serializer):
    destination = serializers.CharField(required=False)
    email = serializers.EmailField(required=False, allow_blank=True)
    phone = serializers.CharField(required=False, allow_blank=True)
    code = serializers.CharField(max_length=10)
    mode = serializers.ChoiceField(choices=["signin", "signup"], default="signup")
    # No default: `default="owner"` would make every client that omits the field
    # a merchant, which is the phantom-shop bug this exists to close. Absent
    # means "infer from shop_name" -- see `validate`.
    role = serializers.ChoiceField(
        choices=["buyer", "owner"], required=False, allow_blank=True
    )
    shop_name = serializers.CharField(required=False, allow_blank=True, max_length=200)
    city = serializers.CharField(required=False, allow_blank=True, max_length=100)
    username = serializers.CharField(required=False, allow_blank=True, max_length=150)
    # The passwordless door creates accounts too, so it collects the name on the
    # same terms as `RegistrationSerializer` -- otherwise half of all signups
    # arrive nameless.
    full_name = serializers.CharField(required=False, allow_blank=True, max_length=150)
    latitude = serializers.DecimalField(
        max_digits=9,
        decimal_places=6,
        required=False,
        allow_null=True,
        min_value=Decimal("-90.0"),
        max_value=Decimal("90.0"),
    )
    longitude = serializers.DecimalField(
        max_digits=9,
        decimal_places=6,
        required=False,
        allow_null=True,
        min_value=Decimal("-180.0"),
        max_value=Decimal("180.0"),
    )

    def validate(self, attrs):
        dest = attrs.get("destination") or attrs.get("email") or attrs.get("phone")
        if not dest:
            raise serializers.ValidationError(
                {"destination": "An email address or phone number is required."}
            )
        attrs["destination"] = str(dest).strip()

        if attrs.get("mode") == "signup":
            # Same back-compat shape as RegistrationSerializer: an explicit
            # `role` wins, `shop_name` stands in for clients that send none.
            role = attrs.get("role") or (
                "owner" if (attrs.get("shop_name") or attrs.get("city")) else "buyer"
            )
            attrs["role"] = role
            if role == "owner":
                missing = {
                    field: "This field is required when registering a shop."
                    for field in ("shop_name", "city")
                    if not attrs.get(field)
                }
                if missing:
                    raise serializers.ValidationError(missing)
        return attrs
