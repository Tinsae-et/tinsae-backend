from django.contrib import admin

from .models import Category, Product


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ["name", "parent", "display_order", "slug"]
    prepopulated_fields = {"slug": ["name"]}


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ["brand", "name", "category"]
    list_filter = ["category", "brand"]
    search_fields = ["name", "brand"]
    prepopulated_fields = {"slug": ["name"]}
