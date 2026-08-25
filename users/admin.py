from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from .models import User


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    list_display = ["username", "email", "phone", "is_staff", "last_login"]
    list_filter = BaseUserAdmin.list_filter + ("date_joined",)
    search_fields = ["username", "email", "phone", "telegram_chat_id"]
    fieldsets = BaseUserAdmin.fieldsets + (
        ("Extra Info", {"fields": ("phone", "telegram_chat_id")}),
    )
    # Without this the admin "add user" form has no phone field, so every
    # account created here starts with a NULL phone and one fewer way to log in.
    add_fieldsets = BaseUserAdmin.add_fieldsets + (
        ("Extra Info", {"fields": ("email", "phone")}),
    )
