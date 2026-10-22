from django.contrib import admin, messages
from django.utils import timezone
from django.utils.html import format_html
from django.utils.timezone import localtime

# `bot` depends on `shops`, not the other way round — but the link code is a
# Telegram concept and belongs in `bot`, and the action that mints one has to
# hang off ShopAdmin. Admin modules load after the app registry is populated, so
# this is safe; it mirrors the existing catalog import below.
from bot.services import issue_link_code
from catalog.models import Product

from .models import Shop, VerificationRequest


class ProductInline(admin.TabularInline):
    model = Product
    extra = 0
    fields = ["brand", "name", "price", "last_confirmed_at", "category"]
    autocomplete_fields = ["category"]
    show_change_link = True


@admin.register(VerificationRequest)
class VerificationRequestAdmin(admin.ModelAdmin):
    list_display = (
        "shop",
        "submitted_at",
        "reviewed_at",
        "reviewer",
        "state",
    )
    list_filter = ("state",)
    search_fields = ("shop__name",)
    readonly_fields = ("submitted_at", "reviewed_at")
    actions = ["approve_requests", "reject_requests"]

    @admin.action(description="Approve selected verification requests")
    def approve_requests(self, request, queryset):
        for req in queryset:
            req.state = "approved"
            req.reviewer = request.user
            req.save()
        self.message_user(request, "Selected requests approved.", messages.SUCCESS)

    @admin.action(description="Reject selected verification requests")
    def reject_requests(self, request, queryset):
        for req in queryset:
            req.state = "rejected"
            req.reviewer = request.user
            if not req.rejection_reason:
                req.rejection_reason = (
                    "Application does not meet verification criteria."
                )
            req.save()
        self.message_user(request, "Selected requests rejected.", messages.WARNING)

    def save_model(self, request, obj, form, change):
        if obj.state in ("approved", "rejected") and not obj.reviewer:
            obj.reviewer = request.user
        super().save_model(request, obj, form, change)


@admin.register(Shop)
class ShopAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "city",
        "language",
        "is_verified",
        "telegram_username",
        "telegram_linked",
        "blocked_at",
        "owner",
        "created_at",
    )
    list_filter = (
        "is_verified",
        "language",
        "city",
        # `telegram_chat_id` is blank rather than null when unset, which
        # EmptyFieldListFilter treats as empty — so this reads as linked/unlinked.
        ("telegram_chat_id", admin.EmptyFieldListFilter),
        ("blocked_at", admin.EmptyFieldListFilter),
    )
    search_fields = ("name", "city", "telegram_username")
    prepopulated_fields = {"slug": ("name",)}
    readonly_fields = ("is_verified", "created_at", "updated_at")
    inlines = [ProductInline]
    actions = ["generate_telegram_link_code", "clear_telegram_block"]

    @admin.display(boolean=True, description="Telegram", ordering="telegram_chat_id")
    def telegram_linked(self, obj):
        """Whether the bot has anywhere to send this shop's prompts."""
        return bool(obj.telegram_chat_id)

    @admin.action(description="Generate Telegram link code")
    def generate_telegram_link_code(self, request, queryset):
        """Mint one code per selected shop and show it for copy-paste.

        Shown as a message rather than surfaced on the change form: the code is a
        bearer credential and this is the only place it needs to be readable.
        """
        for shop in queryset:
            link_code = issue_link_code(shop, created_by=request.user)
            self.message_user(
                request,
                format_html(
                    "{}: ask the merchant to send <code>/link {}</code> to the "
                    "bot. Valid until {}.",
                    shop.name,
                    link_code.code,
                    localtime(link_code.expires_at).strftime("%Y-%m-%d %H:%M"),
                ),
                messages.SUCCESS,
            )

    @admin.action(description="Clear Telegram block")
    def clear_telegram_block(self, request, queryset):
        """Set blocked_at=None for the selected shops."""
        count = queryset.update(blocked_at=None, updated_at=timezone.now())
        self.message_user(
            request,
            f"Cleared Telegram block for {count} shop(s).",
            messages.SUCCESS,
        )
