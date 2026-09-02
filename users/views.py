import logging

from rest_framework import generics, status
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from .emails import send_password_reset_email
from .models import User
from .serializers import (
    LogoutSerializer,
    MultiIdentifierTokenObtainPairSerializer,
    OTPRequestSerializer,
    OTPVerifySerializer,
    PasswordChangeSerializer,
    PasswordResetConfirmSerializer,
    PasswordResetRequestSerializer,
    RegistrationSerializer,
    UserSerializer,
)
from .services import request_otp, revoke_all_refresh_tokens, verify_otp
from .throttles import LoginAccountThrottle, LoginIPThrottle

logger = logging.getLogger(__name__)


def token_pair(user):
    refresh = RefreshToken.for_user(user)
    return {"access": str(refresh.access_token), "refresh": str(refresh)}


class ThrottledTokenObtainPairView(TokenObtainPairView):
    """Login. Accepts a username, an email or a phone number as `identifier`.

    Two throttles rather than one scope. A single per-IP limit is wrong in both
    directions here: it locks out a whole NAT'd carrier block on legitimate
    traffic, while leaving an attacker who spreads one account's guesses across
    many IPs entirely unlimited. `LoginIPThrottle` is the loose outer bound and
    `LoginAccountThrottle` is the one that actually protects an account.

    simplejwt's single generic "No active account found with the given
    credentials" error still covers both the unknown-identifier and
    wrong-password cases, so login does not distinguish them.
    """

    serializer_class = MultiIdentifierTokenObtainPairSerializer
    throttle_classes = [LoginIPThrottle, LoginAccountThrottle]


class ThrottledTokenRefreshView(TokenRefreshView):
    """simplejwt's refresh view, rate limited.

    Unthrottled previously: every other credential-adjacent endpoint was capped
    and this one was not, which made it the cheapest thing on the API to grind.
    """

    throttle_scope = "token_refresh"


class RegistrationView(generics.CreateAPIView):
    """Register a buyer, or a merchant and their first shop.

    Which one is decided by `role` in the body -- see `RegistrationSerializer`.
    """

    queryset = User.objects.all()
    permission_classes = [AllowAny]
    serializer_class = RegistrationSerializer
    throttle_scope = "register"

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()

        # Tokens in the 201 so the client lands on the dashboard directly rather
        # than replaying the credentials it just sent against /auth/token/.
        return Response(
            {
                **token_pair(user),
                # Context, so `avatar` serialises to an absolute URL here as it
                # does from /me/. Without it this response says `/media/...`
                # and that one says `http://host/media/...` for the same user.
                "user": UserSerializer(user, context={"request": request}).data,
            },
            status=status.HTTP_201_CREATED,
        )


# The old name, kept so existing imports keep working.
ShopOwnerRegistrationView = RegistrationView


class LogoutView(APIView):
    """Blacklist one refresh token — the session this client is holding.

    Does not touch the caller's access token, which stays valid until it expires;
    see `users.services.revoke_all_refresh_tokens` for why that is inherent
    rather than an oversight.
    """

    permission_classes = [IsAuthenticated]
    serializer_class = LogoutSerializer

    def post(self, request):
        serializer = LogoutSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        token = serializer.validated_data["refresh"]

        # A valid token is not automatically *your* token. Without this check any
        # authenticated user who came by another user's refresh token could
        # revoke that user's session.
        if str(token.payload.get("user_id")) != str(request.user.pk):
            raise PermissionDenied("That refresh token belongs to another account.")

        token.blacklist()
        return Response(status=status.HTTP_205_RESET_CONTENT)


class PasswordChangeView(APIView):
    permission_classes = [IsAuthenticated]
    serializer_class = PasswordChangeSerializer

    def post(self, request):
        serializer = PasswordChangeSerializer(
            data=request.data, context={"request": request}
        )
        serializer.is_valid(raise_exception=True)

        user = request.user
        user.set_password(serializer.validated_data["new_password"])
        user.save(update_fields=["password"])

        # Revoke first, then mint: doing it the other way blacklists the pair we
        # are about to hand back.
        revoke_all_refresh_tokens(user)
        return Response(token_pair(user), status=status.HTTP_200_OK)


class PasswordResetRequestView(APIView):
    """Start a reset. Always answers 200, whether or not the email is known.

    Login already refuses to distinguish an unknown account from a wrong
    password; an endpoint here that 404'd on unknown emails would hand back the
    enumeration oracle that protection exists to close.
    """

    permission_classes = [AllowAny]
    throttle_scope = "password_reset"
    serializer_class = PasswordResetRequestSerializer

    def post(self, request):
        serializer = PasswordResetRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        user = User.objects.filter(
            email__iexact=serializer.validated_data["email"], is_active=True
        ).first()
        if user is not None:
            try:
                send_password_reset_email(user)
            except Exception:
                # A broken SMTP server must not become a 500 here: that would
                # distinguish a registered address from an unregistered one just
                # as loudly as a 404 would.
                logger.exception("Password reset email failed for user %s", user.pk)

        return Response(
            {"detail": "If that email is registered, a reset link has been sent."},
            status=status.HTTP_200_OK,
        )


class PasswordResetConfirmView(APIView):
    permission_classes = [AllowAny]
    throttle_scope = "password_reset"
    serializer_class = PasswordResetConfirmSerializer

    def post(self, request):
        serializer = PasswordResetConfirmSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        user = serializer.validated_data["user"]
        user.set_password(serializer.validated_data["new_password"])
        user.save(update_fields=["password"])

        # Whoever forced the reset should not keep a session minted beforehand.
        revoke_all_refresh_tokens(user)
        return Response(
            {"detail": "Password has been reset."}, status=status.HTTP_200_OK
        )


class UserProfileView(generics.RetrieveUpdateAPIView):
    """Read or update the authenticated user's profile.

    `email` and `phone` are read-only in the serializer — an unverified change
    to either is an account takeover, not a profile edit.
    """

    serializer_class = UserSerializer
    permission_classes = [IsAuthenticated]
    throttle_scope = "profile_update"

    def get_object(self):
        # The authenticated user, never a lookup — there is no pk in the URL, so
        # one user's data cannot be requested through another's token.
        return self.request.user

    def get_throttles(self):
        # GET /me/ is hit on every page load; only the writes are capped.
        if self.request.method in ("PUT", "PATCH"):
            return super().get_throttles()
        return []


# The old name, kept so existing imports keep working.
GetMyShops = UserProfileView


class OTPRequestView(APIView):
    """Request a 6-digit OTP code for signin or signup.

    Enforces cooldown and rate limits. If mode is "signin" and the account
    does not exist, answers 404 cleanly.
    """

    permission_classes = [AllowAny]
    throttle_scope = "register"

    def post(self, request):
        serializer = OTPRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        destination = serializer.validated_data["destination"]
        mode = serializer.validated_data["mode"]

        result = request_otp(destination=destination, mode=mode)
        if result is None:
            return Response(
                {"detail": "No account found with this email or phone number."},
                status=status.HTTP_404_NOT_FOUND,
            )

        return Response(result, status=status.HTTP_200_OK)


class OTPVerifyView(APIView):
    """Verify an OTP code and return JWT tokens + user data.

    Supports passwordless signin and signup.
    """

    permission_classes = [AllowAny]

    def post(self, request):
        serializer = OTPVerifySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        user = verify_otp(
            destination=serializer.validated_data["destination"],
            code=serializer.validated_data["code"],
            mode=serializer.validated_data["mode"],
            role=serializer.validated_data.get("role"),
            shop_name=serializer.validated_data.get("shop_name"),
            city=serializer.validated_data.get("city"),
            username=serializer.validated_data.get("username"),
            full_name=serializer.validated_data.get("full_name"),
            latitude=serializer.validated_data.get("latitude"),
            longitude=serializer.validated_data.get("longitude"),
        )

        resp_status = (
            status.HTTP_201_CREATED
            if serializer.validated_data["mode"] == "signup"
            else status.HTTP_200_OK
        )
        return Response(
            {
                **token_pair(user),
                # Context, so `avatar` serialises to an absolute URL here as it
                # does from /me/. Without it this response says `/media/...`
                # and that one says `http://host/media/...` for the same user.
                "user": UserSerializer(user, context={"request": request}).data,
            },
            status=resp_status,
        )
