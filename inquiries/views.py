from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models import Sum
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from shops.models import Shop

from .selectors import inquiries_for_buyer, inquiries_for_shop
from .serializers import (
    BuyerInquiryRequestSerializer,
    InquirySerializer,
    ShopInquiryReplySerializer,
)
from .services import close_inquiry, mark_read, start_inquiry


class BuyerInquiryViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = InquirySerializer

    def get_queryset(self):
        return inquiries_for_buyer(self.request.user).prefetch_related("messages")

    def get_serializer_class(self):
        if self.action == "create":
            return BuyerInquiryRequestSerializer
        return super().get_serializer_class()

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            inquiry = start_inquiry(
                request.user,
                data["shop"],
                data["body"],
                data["sent_via"],
                data.get("product"),
            )
        except DjangoValidationError as exc:
            detail = exc.message_dict if hasattr(exc, "message_dict") else exc.messages
            raise ValidationError(detail) from exc

        serializer = InquirySerializer(inquiry, context=self.get_serializer_context())
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    def retrieve(self, request, *args, **kwargs):
        pk = self.kwargs["pk"]
        instance = mark_read(self.request.user, pk)
        serializer = self.get_serializer(instance)
        return Response(serializer.data)

    @action(detail=True, methods=["post"])
    def close(self, request, pk=None):
        pk = self.kwargs["pk"]
        instance = close_inquiry(self.request.user, pk)
        serializer = self.get_serializer(instance)
        return Response(serializer.data, status=status.HTTP_200_OK)

    @action(detail=False, methods=["get"])
    def unreads(self, request):
        unread = inquiries_for_buyer(self.request.user).aggregate(Sum("buyer_unread"))
        return Response(unread, status=status.HTTP_200_OK)


class ShopInquiryViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = InquirySerializer

    def get_serializer_class(self):
        if self.action == "create":
            return ShopInquiryReplySerializer
        return super().get_serializer_class()

    def get_queryset(self):
        shop = Shop.objects.filter(owner=self.request.user).first()
        qs = inquiries_for_shop(shop)
        if self.request.query_params.get("open"):
            qs = qs.filter(status="open")
        if self.request.query_params.get("unread"):
            qs = qs.filter(shop_unread__gt=0)
        return qs

    def retrieve(self, request, *args, **kwargs):
        pk = self.kwargs["pk"]
        instance = mark_read(self.request.user, pk)
        serializer = self.get_serializer(instance)
        return Response(serializer.data, status=status.HTTP_200_OK)

    def create(self, request, *args, **kwargs):
        shop = Shop.objects.filter(owner=self.request.user).first()
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            inquiry = start_inquiry(
                data["buyer"],
                shop,
                data["body"],
                data["sent_via"],
                data.get("product"),
            )
        except DjangoValidationError as exc:
            detail = exc.message_dict if hasattr(exc, "message_dict") else exc.messages
            raise ValidationError(detail) from exc

        serializer = InquirySerializer(inquiry, context=self.get_serializer_context())
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"])
    def close(self, request, pk=None):
        pk = self.kwargs["pk"]
        instance = close_inquiry(self.request.user, pk)
        serializer = self.get_serializer(instance)
        return Response(serializer.data, status=status.HTTP_200_OK)

    @action(detail=False, methods=["get"])
    def unreads(self, request):
        shop = Shop.objects.filter(owner=self.request.user).first()
        unread = inquiries_for_shop(shop).aggregate(Sum("shop_unread"))
        return Response(unread, status=status.HTTP_200_OK)
