import hashlib
import secrets
from datetime import timedelta

from django.conf import settings
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from rest_framework_simplejwt.token_blacklist.models import (
    BlacklistedToken,
    OutstandingToken,
)

from shops.models import Shop

from .emails import send_otp_email
from .models import OTPVerification, User
from .validators import looks_like_phone, normalize_email, normalize_phone

OTP_TTL = timedelta(minutes=10)
OTP_RESEND_COOLDOWN = timedelta(seconds=60)
OTP_MAX_ATTEMPTS = 5


def revoke_all_refresh_tokens(user):
    """Blacklist every outstanding refresh token for `user`. Returns the count.

    Used wherever the password changes: a password change that leaves old
    sessions alive does not actually evict whoever knew the old password.

    Note what this does *not* do. An access token already in a client's hands
    stays valid until it expires (`ACCESS_TOKEN_LIFETIME`). That is inherent to
    stateless JWT -- checking a blacklist on every request would mean a database
    round trip per API call, which is the cost JWT exists to avoid. The short
    access lifetime is what bounds the window; the refresh chain is what this
    actually severs.
    """
    revoked = 0
    for token in OutstandingToken.objects.filter(user=user):
        _, created = BlacklistedToken.objects.get_or_create(token=token)
        if created:
            revoked += 1
    return revoked


def derive_username(seed: str | None) -> str:
    """A unique username from an email, for signups that don't ask for one.

    `username` is still `USERNAME_FIELD` and still unique, so every account needs
    one -- but asking a buyer to invent a handle is friction for an account whose
    only job is to browse. The email local part is the closest thing to a name we
    have.

    A derived name shaped like a phone number would be unreachable at login:
    `MultiIdentifierBackend` routes that shape to the phone column. Hence the
    fallback, which mirrors what `validate_username` refuses from a human.

    Not race-free -- two concurrent signups can settle on the same candidate and
    the loser hits `user_username_ci_unique`. Left as is: it surfaces as an
    IntegrityError rather than as two accounts sharing a name, and the window is
    a few milliseconds wide.
    """
    seed = str(seed or "")
    prefix = seed.split("@")[0][:20] if "@" in seed else ""
    prefix = "".join(c for c in prefix if c.isalnum() or c in "._-")
    if not prefix or looks_like_phone(prefix):
        prefix = "user"

    candidate = prefix
    counter = 1
    while User.objects.filter(username__iexact=candidate).exists():
        counter += 1
        candidate = f"{prefix}{counter}"
    return candidate


def normalize_destination(destination: str) -> str:
    dest = str(destination or "").strip()
    if "@" in dest:
        email = normalize_email(dest)
        if not email:
            raise ValidationError({"destination": "Invalid email address."})
        return email
    try:
        phone = normalize_phone(dest)
        if phone:
            return phone
    except DjangoValidationError as exc:
        raise ValidationError({"destination": list(exc.messages)}) from exc
    raise ValidationError(
        {"destination": "Enter a valid email or Ethiopian phone number."}
    )


def hash_otp_code(code: str) -> str:
    return hashlib.sha256(code.strip().encode()).hexdigest()


def generate_otp_code() -> str:
    return f"{secrets.randbelow(900000) + 100000:06d}"


def request_otp(destination: str, mode: str = "signup") -> dict | None:
    """Issue a 6-digit OTP code for signin or signup.

    Returns a dict with `expires_in` and `resend_in` on success, or None if
    `mode == "signin"` and no account exists for this destination (allowing the
    view to answer 404 rather than spending an email or SMS).
    """
    dest = normalize_destination(destination)
    now = timezone.now()

    # Email is the only channel that can actually deliver. Without this guard a
    # phone destination writes an OTP row, answers 200 with an `expires_in`, and
    # sends nothing -- the user waits for a code that was never dispatched.
    # Lifting this means adding an SMS gateway, not deleting these four lines.
    if "@" not in dest:
        raise ValidationError(
            {
                "destination": "Verification codes can only be sent to an email "
                "address at the moment."
            }
        )

    is_email = "@" in dest
    user_exists = (
        User.objects.filter(email__iexact=dest).exists()
        if is_email
        else User.objects.filter(phone=dest).exists()
    )

    if mode == "signin" and not user_exists:
        return None

    if mode == "signup" and user_exists:
        raise ValidationError(
            {"destination": "An account with this email/phone already exists."}
        )

    # Check resend cooldown
    recent = (
        OTPVerification.objects.filter(
            destination=dest, mode=mode, is_used=False, resend_after__gt=now
        )
        .order_by("-created_at")
        .first()
    )
    if recent is not None:
        wait_sec = max(1, int((recent.resend_after - now).total_seconds()))
        raise ValidationError(
            {
                "resend_in": wait_sec,
                "detail": f"Please wait {wait_sec}s before requesting a new code.",
            }
        )

    code = generate_otp_code()
    OTPVerification.objects.create(
        destination=dest,
        code_hash=hash_otp_code(code),
        mode=mode,
        expires_at=now + OTP_TTL,
        resend_after=now + OTP_RESEND_COOLDOWN,
    )

    if is_email:
        send_otp_email(dest, code)

    result = {
        "expires_in": int(OTP_TTL.total_seconds()),
        "resend_in": int(OTP_RESEND_COOLDOWN.total_seconds()),
    }
    if settings.DEBUG:
        result["dev_code"] = code
    return result


def verify_otp(
    destination: str,
    code: str,
    mode: str = "signup",
    role: str | None = None,
    shop_name: str | None = None,
    city: str | None = None,
    username: str | None = None,
    full_name: str | None = None,
    phone: str | None = None,
    latitude=None,
    longitude=None,
) -> User:
    """Verify an OTP code and return the authenticated/created User."""
    dest = normalize_destination(destination)
    now = timezone.now()

    error = None
    with transaction.atomic():
        # select_for_update because the read-modify-write on `attempts` below is
        # otherwise check-then-act: N concurrent guesses all read the same value
        # and all write back the same increment, so OTP_MAX_ATTEMPTS counts one
        # attempt per round rather than per request.
        otp = (
            OTPVerification.objects.select_for_update()
            .filter(destination=dest, mode=mode, is_used=False)
            .order_by("-created_at")
            .first()
        )

        if otp is None or otp.expires_at < now:
            error = {
                "code": "That verification code is invalid or has expired. Please request a new one."
            }
        elif otp.attempts >= OTP_MAX_ATTEMPTS:
            otp.is_used = True
            otp.save(update_fields=["is_used", "updated_at"])
            error = {"code": "Too many failed attempts. Please request a new code."}
        elif hash_otp_code(str(code)) != otp.code_hash:
            otp.attempts += 1
            fields = ["attempts", "updated_at"]
            if otp.attempts >= OTP_MAX_ATTEMPTS:
                otp.is_used = True
                fields.append("is_used")
                error = {"code": "Too many failed attempts. Please request a new code."}
            else:
                remaining = OTP_MAX_ATTEMPTS - otp.attempts
                error = {
                    "code": f"Invalid verification code. {remaining} attempt(s) remaining."
                }
            otp.save(update_fields=fields)
        else:
            # Valid code -> consume it inside the same lock, so two concurrent
            # correct submissions cannot both proceed to create an account.
            otp.is_used = True
            otp.save(update_fields=["is_used", "updated_at"])

    # Raised outside the block on purpose: a ValidationError thrown inside
    # `atomic()` rolls it back, which would undo the attempts increment above and
    # make the cap unreachable.
    if error is not None:
        raise ValidationError(error)

    if mode == "signin":
        is_email = "@" in dest
        user = (
            User.objects.filter(email__iexact=dest, is_active=True).first()
            if is_email
            else User.objects.filter(phone=dest, is_active=True).first()
        )
        if user is None:
            raise ValidationError(
                {"detail": "No active account found with this identifier."}
            )
        return user

    # mode == "signup" -> create User (and Shop if role == "owner") atomically
    with transaction.atomic():
        is_email = "@" in dest
        user_email = dest if is_email else None
        user_phone = (
            dest if not is_email else (normalize_phone(phone) if phone else None)
        )

        if not username:
            username = derive_username(dest)

        user = User.objects.create_user(
            username=username,
            email=user_email,
            phone=user_phone,
            full_name=full_name or "",
        )
        user.set_unusable_password()
        user.save()

        # A buyer is a User with no Shop, and that is a valid terminal state --
        # `shops: []` is the whole of what "buyer" means and what the frontend
        # routes on. This block used to run unconditionally, falling back to
        # `f"{username}'s Shop"` in "Addis Ababa", so every passwordless signup
        # silently became a merchant with a phantom storefront that then showed
        # up in shop listings and search.
        #
        # `role` is optional only for backward compatibility -- the same shape as
        # the `username`/`identifier` alias in the login serializer. `shop_name`
        # stands in until every client sends `role`; an explicit `role` wins, so
        # a dropped `shop_name` in an owner signup is a 400 and not a silent
        # buyer account. `OTPVerifySerializer` guarantees both fields are present
        # by the time we get here, which is why there are no defaults left.
        is_owner = role == "owner" if role else bool(shop_name or city)
        if is_owner:
            Shop.objects.create(
                name=shop_name,
                city=city,
                phone=user_phone or "",
                latitude=latitude,
                longitude=longitude,
                owner=user,
            )

    return user
