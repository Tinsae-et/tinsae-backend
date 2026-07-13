from django.contrib import admin

from catalog.models import Product

from .models import Shop


class ProductInline(admin.TabularInline):
    model = Product
    extra = 0
    fields = ["brand", "name", "price", "freshness", "category"]
    autocomplete_fields = ["category"]
    show_change_link = True


@admin.register(Shop)
class ShopAdmin(admin.ModelAdmin):
    list_display = ("name", "city", "is_verified", "owner", "created_at")
    list_filter = ("is_verified", "city")
    search_fields = ("name", "city")
    prepopulated_fields = {"slug": ("name",)}
    readonly_fields = ("created_at", "updated_at")
    inlines = [ProductInline]
