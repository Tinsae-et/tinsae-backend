from django.contrib import admin

from .models import ContactIntent, Inquiry, InquiryMessage


class InquiryMessageInline(admin.TabularInline):
    model = InquiryMessage
    extra = 0
    fields = ["created_at", "sender_role", "sent_via", "body"]
    readonly_fields = fields
    can_delete = False


@admin.register(Inquiry)
class InquiryAdmin(admin.ModelAdmin):
    list_display = [
        "id",
        "buyer",
        "shop",
        "product",
        "status",
        "last_sender_role",
        "last_message_at",
        "buyer_unread",
        "shop_unread",
    ]
    list_filter = ["status", "last_sender_role"]
    search_fields = ["buyer__username", "shop__name", "product__name"]
    raw_id_fields = ["buyer", "shop", "product"]
    readonly_fields = ["created_at", "updated_at"]
    list_select_related = ["buyer", "shop", "product"]
    inlines = [InquiryMessageInline]


@admin.register(InquiryMessage)
class InquiryMessageAdmin(admin.ModelAdmin):
    list_display = ["id", "inquiry", "sender_role", "sent_via", "created_at"]
    list_filter = ["sender_role", "sent_via"]
    search_fields = ["body"]
    raw_id_fields = ["inquiry"]
    readonly_fields = ["created_at", "updated_at"]


@admin.register(ContactIntent)
class ContactIntentAdmin(admin.ModelAdmin):
    list_display = [
        "id",
        "shop",
        "product",
        "channel",
        "user",
        "ip_hash",
        "created_at",
    ]
    list_filter = ["channel", "created_at"]
    search_fields = ["shop__name", "product__name", "user__username"]
    raw_id_fields = ["shop", "product", "user"]
    readonly_fields = ["created_at", "updated_at"]
