from rest_framework import generics, status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenObtainPairView

from .models import User
from .serializers import ShopOwnerRegistrationSerializer, UserSerializer


class ThrottledTokenObtainPairView(TokenObtainPairView):
    """simplejwt's login view with a throttle scope attached.

    Subclassed rather than configured: `DEFAULT_THROTTLE_RATES` keys are matched
    by `throttle_scope`, and the stock view does not set one. Nothing else about
    the view changes -- in particular its single generic "No active account
    found with the given credentials" error already covers both the unknown-user
    and wrong-password cases, so login does not distinguish them.
    """

    throttle_scope = "login"


class ShopOwnerRegistrationView(generics.CreateAPIView):
    queryset = User.objects.all()
    permission_classes = [AllowAny]
    serializer_class = ShopOwnerRegistrationSerializer
    throttle_scope = "register"

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()

        # Tokens in the 201 so the client lands on the dashboard directly rather
        # than replaying the credentials it just sent against /auth/token/.
        refresh = RefreshToken.for_user(user)
        return Response(
            {
                "access": str(refresh.access_token),
                "refresh": str(refresh),
                "user": UserSerializer(user).data,
            },
            status=status.HTTP_201_CREATED,
        )


class GetMyShops(generics.RetrieveAPIView):
    serializer_class = UserSerializer
    permission_classes = [IsAuthenticated]
    queryset = User.objects.all()

    def get_object(self):
        # The authenticated user, never a lookup — there is no pk in the URL, so
        # one user's data cannot be requested through another's token.
        return self.request.user
