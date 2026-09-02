from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from .models import User


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    list_display = ["username", "full_name", "email", "phone", "is_staff", "last_login"]
    list_filter = BaseUserAdmin.list_filter + ("date_joined",)
    search_fields = [
        "username",
        "full_name",
        "email",
        "phone",
        "telegram_username",
        "telegram_chat_id",
    ]
    # Rebuilt rather than appended to. BaseUserAdmin's "Personal info" block is
    # first_name/last_name/email, and nothing reads first_name or last_name any
    # more -- `get_full_name` and the outgoing mail both go through `full_name`.
    # Left in place they are two boxes a staff member can fill to no effect.
    fieldsets = (
        (None, {"fields": ("username", "password")}),
        (
            "Personal info",
            {"fields": ("full_name", "email", "phone", "preferred_area", "avatar")},
        ),
        ("Telegram", {"fields": ("telegram_username", "telegram_chat_id")}),
        (
            "Permissions",
            {
                "fields": (
                    "is_active",
                    "is_staff",
                    "is_superuser",
                    "groups",
                    "user_permissions",
                )
            },
        ),
        ("Important dates", {"fields": ("last_login", "date_joined")}),
    )
    # Without this the admin "add user" form has no phone field, so every
    # account created here starts with a NULL phone and one fewer way to log in.
    add_fieldsets = BaseUserAdmin.add_fieldsets + (
        ("Extra Info", {"fields": ("email", "phone")}),
    )
