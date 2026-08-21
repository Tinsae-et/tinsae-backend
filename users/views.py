from rest_framework import generics, status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response

from .models import User
from .serializers import ShopOwnerRegistrationSerializer, UserSerializer


class ShopOwnerRegistrationView(generics.CreateAPIView):
    queryset = User.objects.all()
    permission_classes = [AllowAny]
    serializer_class = ShopOwnerRegistrationSerializer

    def create(self, request, *args, **kwargs):
        serializer = ShopOwnerRegistrationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save()

        return Response(
            {
                "message": "Shop owner registered successfully.",
            },
            status=status.HTTP_201_CREATED,
        )


class GetMyShops(generics.RetrieveAPIView):
    serializer_class = UserSerializer
    permission_classes = [IsAuthenticated]
    queryset = User.objects.all()

    def get_object(self):
        return self.request.user
