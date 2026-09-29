from django.conf import settings
from django.shortcuts import get_object_or_404
from rest_framework import permissions, serializers, status
from rest_framework.response import Response
from rest_framework.views import APIView

from shops.models import Shop

from .services import disconnect_shop, issue_link_code


class TelegramConnectResponseSerializer(serializers.Serializer):
    code = serializers.CharField()
    deep_link = serializers.CharField(allow_null=True)
    bot_username = serializers.CharField(allow_blank=True)
    expires_at = serializers.DateTimeField()


class TelegramStatusResponseSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=["idle", "connected", "blocked"])
    linked_at = serializers.DateTimeField(allow_null=True)


class TelegramConnectView(APIView):
    """Generate a ShopLinkCode and deep link for connecting the shop to Telegram."""

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, slug):
        shop = get_object_or_404(Shop, slug=slug, owner=request.user)
        link_code = issue_link_code(shop, created_by=request.user)

        bot_username = getattr(settings, "TELEGRAM_BOT_USERNAME", "").lstrip("@")
        deep_link = (
            f"https://t.me/{bot_username}?start={link_code.code}"
            if bot_username
            else None
        )

        data = {
            "code": link_code.code,
            "deep_link": deep_link,
            "bot_username": bot_username,
            "expires_at": link_code.expires_at,
        }
        return Response(
            TelegramConnectResponseSerializer(data).data,
            status=status.HTTP_200_OK,
        )


class TelegramStatusView(APIView):
    """Return the current Telegram connection status and last linked timestamp."""

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, slug):
        shop = get_object_or_404(Shop, slug=slug, owner=request.user)

        if shop.blocked_at is not None:
            tele_status = "blocked"
        elif shop.telegram_chat_id:
            tele_status = "connected"
        else:
            tele_status = "idle"

        last_used = (
            shop.link_codes.filter(used_at__isnull=False)
            .order_by("-used_at")
            .values_list("used_at", flat=True)
            .first()
        )

        data = {
            "status": tele_status,
            "linked_at": last_used,
        }
        return Response(
            TelegramStatusResponseSerializer(data).data,
            status=status.HTTP_200_OK,
        )


class TelegramDisconnectView(APIView):
    """Disconnect the shop from Telegram and clear blocked state."""

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, slug):
        shop = get_object_or_404(Shop, slug=slug, owner=request.user)
        disconnect_shop(shop)
        return Response(status=status.HTTP_204_NO_CONTENT)
