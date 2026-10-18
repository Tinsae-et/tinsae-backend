from django.contrib import admin, messages
from django.utils import timezone

from .models import (
    ConfirmationDigest,
    ConfirmationRequest,
    OutboundMessage,
    ProcessedUpdate,
    ShopLinkCode,
)


@admin.register(ConfirmationDigest)
class ConfirmationDigestAdmin(admin.ModelAdmin):
    list_display = [
        "id",
        "shop",
        "for_date",
        "review_cursor",
        "reminded_at",
        "closed_at",
        "created_at",
    ]
    list_filter = ["for_date", "closed_at", "reminded_at", "shop"]
    search_fields = ["shop__name", "shop__telegram_chat_id", "telegram_message_id"]
    raw_id_fields = ["shop"]
    readonly_fields = ["created_at", "updated_at"]
    list_select_related = ["shop"]


@admin.register(ConfirmationRequest)
class ConfirmationRequestAdmin(admin.ModelAdmin):
    list_display = [
        "id",
        "product",
        "shop",
        "digest",
        "status",
        "channel",
        "expires_at",
        "created_at",
        "responded_at",
    ]
    list_filter = ["status", "shop", "channel", "created_at", "expires_at"]
    search_fields = ["product__name", "shop__name", "telegram_message_id"]
    raw_id_fields = ["product", "shop", "digest"]
    readonly_fields = ["created_at", "updated_at", "responded_at"]
    list_select_related = ["product", "shop", "digest"]


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


@admin.register(OutboundMessage)
class OutboundMessageAdmin(admin.ModelAdmin):
    list_display = [
        "id",
        "chat_id",
        "kind",
        "status",
        "attempts",
        "next_attempt_at",
        "send_silently_at_night",
        "telegram_message_id",
        "created_at",
    ]
    list_filter = [
        "status",
        "kind",
        "send_silently_at_night",
        "created_at",
        "next_attempt_at",
    ]
    search_fields = ["chat_id", "dedupe_key", "telegram_message_id", "last_error"]
    readonly_fields = ["created_at", "updated_at", "claimed_at"]
    actions = ["requeue_messages"]

    @admin.action(description="Requeue selected dead or cancelled messages")
    def requeue_messages(self, request, queryset):
        requeued = queryset.filter(status__in=["dead", "cancelled"]).update(
            status="queued",
            next_attempt_at=timezone.now(),
            last_error="",
            claimed_at=None,
        )
        self.message_user(
            request,
            f"Successfully requeued {requeued} message(s).",
            messages.SUCCESS,
        )


@admin.register(ProcessedUpdate)
class ProcessedUpdateAdmin(admin.ModelAdmin):
    list_display = ["update_id", "created_at"]
    list_filter = ["created_at"]
    search_fields = ["update_id"]
    readonly_fields = ["update_id", "created_at", "updated_at"]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
