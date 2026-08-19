from django.contrib import admin

from .models import ConfirmationRequest


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
