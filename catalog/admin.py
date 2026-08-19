from django.contrib import admin

from .models import Category, ConfirmationRequest, Product, ProductImage


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ["name", "parent", "display_order", "slug"]
    search_fields = ["name"]
    prepopulated_fields = {"slug": ["name"]}


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = [
        "brand",
        "name",
        "price",
        "confirmation_status",
        "last_confirmed_at",
        "is_available",
        "shop",
        "category",
    ]
    list_filter = ["confirmation_status", "is_available", "category", "brand", "shop"]
    search_fields = ["name", "brand"]
    prepopulated_fields = {"slug": ["name"]}
    autocomplete_fields = ["shop", "category"]


class ProductImageInline(admin.TabularInline):
    model = ProductImage
    extra = 1
    fields = ["image", "is_primary", "display_order"]


@admin.register(ConfirmationRequest)
class ConfirmationRequestAdmin(admin.ModelAdmin):
    list_display = ["product", "shop", "status", "sent_at", "responded_at"]
    list_filter = ["shop__name", "status", "sent_at"]


inlines = [ProductImageInline]
