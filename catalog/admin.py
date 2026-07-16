from django.contrib import admin

from .models import Category, Product


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ["name", "parent", "display_order", "slug"]
    search_fields = ["name"]
    prepopulated_fields = {"slug": ["name"]}


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ["brand", "name", "price", "last_confirmed_at", "shop", "category"]
    list_filter = ["category", "brand", "last_confirmed_at", "shop"]
    search_fields = ["name", "brand"]
    prepopulated_fields = {"slug": ["name"]}
    autocomplete_fields = ["shop", "category"]
