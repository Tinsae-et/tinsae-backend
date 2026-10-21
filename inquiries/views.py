from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models import Sum
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .models import Inquiry, InquiryMessage
from .selectors import (
    get_inquiry_for_buyer,
    inquiries_for_buyer,
    inquiries_for_owner,
)
from .serializers import (
    BuyerInquiryRequestSerializer,
    InquiryReplySerializer,
    InquirySerializer,
)
from .services import close_inquiry, mark_read, send_message, start_inquiry


class BuyerInquiryViewSet(
    mixins.CreateModelMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    permission_classes = [IsAuthenticated]
    serializer_class = InquirySerializer
    throttle_scope = "inquiries"

    def get_queryset(self):
        return inquiries_for_buyer(self.request.user).prefetch_related("messages")

    def get_serializer_class(self):
        if self.action == "create":
            return BuyerInquiryRequestSerializer
        if self.action == "reply":
            return InquiryReplySerializer
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
                data.get("sent_via", InquiryMessage.SentVia.WEB),
                data.get("product"),
            )
        except DjangoValidationError as exc:
            detail = exc.message_dict if hasattr(exc, "message_dict") else exc.messages
            raise ValidationError(detail) from exc

        serializer = InquirySerializer(inquiry, context=self.get_serializer_context())
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    def retrieve(self, request, *args, **kwargs):
        pk = self.kwargs["pk"]
        try:
            get_inquiry_for_buyer(self.request.user, pk)
            instance = mark_read(self.request.user, pk)
        except Inquiry.DoesNotExist as exc:
            raise NotFound("Inquiry not found.") from exc
        serializer = self.get_serializer(instance)
        return Response(serializer.data)

    @action(detail=True, methods=["post"])
    def reply(self, request, pk=None):
        pk = self.kwargs["pk"]
        try:
            get_inquiry_for_buyer(self.request.user, pk)
        except Inquiry.DoesNotExist as exc:
            raise NotFound("Inquiry not found.") from exc

        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            send_message(
                self.request.user,
                pk,
                data["body"],
                data.get("sent_via", InquiryMessage.SentVia.WEB),
            )
        except DjangoValidationError as exc:
            detail = exc.message_dict if hasattr(exc, "message_dict") else exc.messages
            raise ValidationError(detail) from exc

        inquiry = get_inquiry_for_buyer(self.request.user, pk)
        response_serializer = InquirySerializer(
            inquiry, context=self.get_serializer_context()
        )
        return Response(response_serializer.data, status=status.HTTP_200_OK)

    @action(detail=True, methods=["post"])
    def close(self, request, pk=None):
        pk = self.kwargs["pk"]
        try:
            get_inquiry_for_buyer(self.request.user, pk)
            instance = close_inquiry(self.request.user, pk)
        except Inquiry.DoesNotExist as exc:
            raise NotFound("Inquiry not found.") from exc
        serializer = self.get_serializer(instance)
        return Response(serializer.data, status=status.HTTP_200_OK)

    @action(detail=False, methods=["get"])
    def unreads(self, request):
        total = (
            inquiries_for_buyer(self.request.user).aggregate(total=Sum("buyer_unread"))[
                "total"
            ]
            or 0
        )
        return Response({"unread_count": total}, status=status.HTTP_200_OK)


class ShopInquiryViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    permission_classes = [IsAuthenticated]
    serializer_class = InquirySerializer
    throttle_scope = "inquiries"

    def get_serializer_class(self):
        if self.action == "reply":
            return InquiryReplySerializer
        return super().get_serializer_class()

    def get_queryset(self):
        qs = inquiries_for_owner(self.request.user).prefetch_related("messages")
        open_param = self.request.query_params.get("open")
        if open_param in ("1", "true", "True"):
            qs = qs.filter(status=Inquiry.Status.OPEN)
        elif open_param in ("0", "false", "False"):
            qs = qs.filter(status=Inquiry.Status.CLOSED)

        unread_param = self.request.query_params.get("unread")
        if unread_param in ("1", "true", "True"):
            qs = qs.filter(shop_unread__gt=0)
        elif unread_param in ("0", "false", "False"):
            qs = qs.filter(shop_unread=0)
        return qs

    def retrieve(self, request, *args, **kwargs):
        pk = self.kwargs["pk"]
        try:
            inquiries_for_owner(self.request.user).get(pk=pk)
            instance = mark_read(self.request.user, pk)
        except Inquiry.DoesNotExist as exc:
            raise NotFound("Inquiry not found.") from exc
        serializer = self.get_serializer(instance)
        return Response(serializer.data, status=status.HTTP_200_OK)

    @action(detail=True, methods=["post"])
    def reply(self, request, pk=None):
        pk = self.kwargs["pk"]
        try:
            inquiries_for_owner(self.request.user).get(pk=pk)
        except Inquiry.DoesNotExist as exc:
            raise NotFound("Inquiry not found.") from exc

        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            send_message(
                self.request.user,
                pk,
                data["body"],
                data.get("sent_via", InquiryMessage.SentVia.WEB),
            )
        except DjangoValidationError as exc:
            detail = exc.message_dict if hasattr(exc, "message_dict") else exc.messages
            raise ValidationError(detail) from exc

        inquiry = inquiries_for_owner(self.request.user).get(pk=pk)
        response_serializer = InquirySerializer(
            inquiry, context=self.get_serializer_context()
        )
        return Response(response_serializer.data, status=status.HTTP_200_OK)

    @action(detail=True, methods=["post"])
    def close(self, request, pk=None):
        pk = self.kwargs["pk"]
        try:
            inquiries_for_owner(self.request.user).get(pk=pk)
            instance = close_inquiry(self.request.user, pk)
        except Inquiry.DoesNotExist as exc:
            raise NotFound("Inquiry not found.") from exc
        serializer = self.get_serializer(instance)
        return Response(serializer.data, status=status.HTTP_200_OK)

    @action(detail=False, methods=["get"])
    def unreads(self, request):
        total = (
            inquiries_for_owner(self.request.user).aggregate(total=Sum("shop_unread"))[
                "total"
            ]
            or 0
        )
        return Response({"unread_count": total}, status=status.HTTP_200_OK)
