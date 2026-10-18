"""Admin configuration for notifications."""

from django.contrib import admin

from .models import Notification


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = [
        "id",
        "recipient",
        "kind",
        "created_at",
        "read_at",
        "email_sent_at",
    ]
    list_filter = ["kind", "read_at", "email_sent_at"]
    search_fields = ["recipient__username", "dedupe_key"]
    raw_id_fields = ["recipient"]
    readonly_fields = ["created_at", "updated_at", "read_at", "email_sent_at"]
    list_select_related = ["recipient"]
