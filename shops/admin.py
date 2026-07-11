from django.contrib import admin

from .models import Shop


@admin.register(Shop)
class ShopAdmin(admin.ModelAdmin):
    list_display = ("name", "city", "is_verified", "created_at")
    list_filter = ("is_verified", "city")
    search_fields = ("name", "city")
    prepopulated_fields = {"slug": ("name",)}
    readonly_fields = ("created_at", "updated_at")
