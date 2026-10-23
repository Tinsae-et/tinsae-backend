from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models import Sum
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import BaseThrottle, ScopedRateThrottle
from rest_framework.views import APIView

from .models import Inquiry, InquiryMessage
from .selectors import (
    get_inquiry_for_buyer,
    inquiries_for_buyer,
    inquiries_for_owner,
    with_inbox_relations,
)
from .serializers import (
    BuyerInquiryRequestSerializer,
    ContactIntentSerializer,
    InquiryReplySerializer,
    InquirySerializer,
)
from .services import (
    close_inquiry,
    mark_read,
    record_contact_intent,
    send_message,
    start_inquiry,
)
from .throttles import PhoneRevealUserThrottle

WEB = InquiryMessage.SentVia.WEB


def _client_ip(request):
    """The caller's address, by the same NUM_PROXIES rule DRF's throttles use.

    The left end of X-Forwarded-For is the one hop the client writes itself;
    reading it let anyone choose which IP a lead was counted under.
    """
    return BaseThrottle().get_ident(request) or ""


def _user_agent(request):
    return request.META.get("HTTP_USER_AGENT", "")


class SendThrottleMixin:
    """Sending gets its own, tighter bucket than reading.

    Every message notifies the other side -- for a merchant, a Telegram ping --
    so the read scope's 60/min would let one buyer page a shop every second.
    """

    send_actions = ("create", "reply")

    def get_throttles(self):
        if self.action in self.send_actions:
            self.throttle_scope = "inquiry_send"
        return super().get_throttles()


class BuyerInquiryViewSet(
    SendThrottleMixin,
    mixins.CreateModelMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    permission_classes = [IsAuthenticated]
    serializer_class = InquirySerializer
    throttle_scope = "inquiries"
    # Ids are integers: anything else is the router's 404, not a ValueError
    # (500) from the pk lookup.
    lookup_value_regex = r"\d+"

    def get_queryset(self):
        return with_inbox_relations(inquiries_for_buyer(self.request.user))

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
                WEB,
                data.get("product"),
                client_ip=_client_ip(request),
                user_agent=_user_agent(request),
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
        try:
            send_message(self.request.user, pk, serializer.validated_data["body"], WEB)
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
    SendThrottleMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    permission_classes = [IsAuthenticated]
    serializer_class = InquirySerializer
    throttle_scope = "inquiries"
    lookup_value_regex = r"\d+"

    def get_serializer_class(self):
        if self.action == "reply":
            return InquiryReplySerializer
        return super().get_serializer_class()

    def get_queryset(self):
        qs = with_inbox_relations(inquiries_for_owner(self.request.user))
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
        try:
            send_message(self.request.user, pk, serializer.validated_data["body"], WEB)
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


class ContactIntentView(APIView):
    """Record a buyer's intent to contact a shop and return the target action link.

    Channel options:
    - phone: requires authenticated user, returns `tel:<number>`.
    - telegram: returns `https://t.me/<username>`.
    - in_app: returns `in_app`.

    Bot defenses:
    - Honeypot check rejects automated bots that populate hidden inputs.
    - User-Agent filter blocks automated scraping frameworks.
    - `contact_intent` scope caps every caller, anonymous included.
    - PhoneRevealUserThrottle limits per-user phone number reveals.
    """

    permission_classes = [AllowAny]
    # Both, explicitly: `throttle_classes` replaces the project default, so
    # listing PhoneRevealUserThrottle alone left `throttle_scope` dead and
    # anonymous callers unlimited.
    throttle_classes = [ScopedRateThrottle, PhoneRevealUserThrottle]
    throttle_scope = "contact_intent"

    def post(self, request):
        serializer = ContactIntentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        try:
            result = record_contact_intent(
                shop=data["shop"],
                channel=data["channel"],
                user=request.user,
                product=data.get("product"),
                client_ip=_client_ip(request),
                user_agent=_user_agent(request),
                honeypot=data.get("honeypot", ""),
            )
        except DjangoValidationError as exc:
            detail = exc.message_dict if hasattr(exc, "message_dict") else exc.messages
            raise ValidationError(detail) from exc

        return Response(result, status=status.HTTP_200_OK)
