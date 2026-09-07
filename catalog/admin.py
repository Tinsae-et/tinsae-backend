from django.contrib import admin

from .models import Category, Product, ProductImage, SearchLog


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ["name", "parent", "display_order", "slug"]
    search_fields = ["name"]
    prepopulated_fields = {"slug": ["name"]}


class ProductImageInline(admin.TabularInline):
    model = ProductImage
    extra = 1
    fields = ["image", "is_primary", "display_order"]


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = [
        "brand",
        "name",
        "condition",
        "price",
        "confirmation_status",
        "last_confirmed_at",
        "is_available",
        "shop",
        "category",
    ]
    list_filter = [
        "condition",
        "confirmation_status",
        "is_available",
        "category",
        "brand",
        "shop",
    ]
    search_fields = ["name", "brand"]
    prepopulated_fields = {"slug": ["name"]}
    autocomplete_fields = ["shop", "category"]
    inlines = [ProductImageInline]


@admin.register(SearchLog)
class SearchLogAdmin(admin.ModelAdmin):
    list_display = ["raw_query", "result_count", "user", "created_at"]
    # `result_count` first because the zero-result rows are what this table is
    # for: filter to them, sort by frequency, and the list is a ranked backlog
    # of missing synonyms and aliases.
    list_filter = ["result_count", "created_at"]
    search_fields = ["raw_query", "normalized"]
    readonly_fields = ["raw_query", "normalized", "result_count", "user"]

    def has_add_permission(self, request):
        return False
