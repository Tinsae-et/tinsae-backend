from django.contrib import admin

from .models import ConfirmationRequest, ShopLinkCode


@admin.register(ConfirmationRequest)
class ConfirmationRequestAdmin(admin.ModelAdmin):
    list_display = [
        "id",
        "product",
        "shop",
        "status",
        "channel",
        "created_at",
        "responded_at",
    ]
    list_filter = ["status", "shop", "channel", "created_at"]
    search_fields = ["product__name", "shop__name", "telegram_message_id"]
    raw_id_fields = ["product", "shop"]
    readonly_fields = ["created_at", "updated_at", "responded_at"]
    list_select_related = ["product", "shop"]


@admin.register(ShopLinkCode)
class ShopLinkCodeAdmin(admin.ModelAdmin):
    """Audit view. Codes are issued from the Shop changelist action, not here."""

    list_display = [
        "code",
        "shop",
        "created_at",
        "expires_at",
        "used_at",
        "used_by_chat_id",
        "created_by",
    ]
    list_filter = ["expires_at", "used_at", "shop"]
    search_fields = ["code", "shop__name", "used_by_chat_id"]
    raw_id_fields = ["shop", "created_by"]
    # `code` is generated and the `used_*` pair is written by the bot. Editing
    # either by hand would make the audit trail lie about who spent what.
    readonly_fields = [
        "code",
        "used_at",
        "used_by_chat_id",
        "created_at",
        "updated_at",
    ]
    list_select_related = ["shop", "created_by"]
