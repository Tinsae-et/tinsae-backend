from django import forms
from django.contrib import admin

from .models import (
    Attribute,
    AttributeOption,
    Category,
    Product,
    ProductCondition,
    ProductImage,
    ProductStatus,
    ProductType,
    ProductTypeAttribute,
    SearchLog,
)
from .specs import category_schema, normalize_specs, validate_specs


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ["name", "parent", "product_type", "icon", "display_order", "slug"]
    list_filter = ["product_type"]
    search_fields = ["name"]
    prepopulated_fields = {"slug": ["name"]}
    # Linked by `manage.py sync_taxonomy`, like the rest of the taxonomy.
    readonly_fields = ["product_type"]


class TaxonomyReadOnlyAdmin(admin.ModelAdmin):
    """Managed in code: catalog/taxonomy/, applied by `manage.py sync_taxonomy`.

    Editing here would be undone by the next sync and would skip review, so the
    admin is for looking, not changing.
    """

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class AttributeOptionInline(admin.TabularInline):
    model = AttributeOption
    fields = ["value", "label", "display_order", "is_active"]
    extra = 0

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Attribute)
class AttributeAdmin(TaxonomyReadOnlyAdmin):
    list_display = ["key", "label", "type", "unit", "min_value", "max_value"]
    list_filter = ["type"]
    search_fields = ["key", "label"]
    inlines = [AttributeOptionInline]


class ProductTypeAttributeInline(admin.TabularInline):
    model = ProductTypeAttribute
    fields = [
        "display_order",
        "attribute",
        "label",
        "required",
        "multiple",
        "required_if",
        "show_if",
        "options",
        "control",
    ]
    extra = 0

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(ProductType)
class ProductTypeAdmin(TaxonomyReadOnlyAdmin):
    list_display = ["key", "label"]
    search_fields = ["key", "label"]
    inlines = [ProductTypeAttributeInline]


class ProductImageInline(admin.TabularInline):
    model = ProductImage
    extra = 1
    fields = ["image", "is_primary", "display_order"]


class ProductAdminForm(forms.ModelForm):
    """Hold admin edits to the same rules as the shop dashboard.

    `specs` is a raw JSON box here; without this an admin could save values no
    merchant form would accept (an unknown key, "fast" as a mileage).
    """

    class Meta:
        model = Product
        fields = "__all__"

    def clean(self):
        cleaned = super().clean()
        category = cleaned.get("category")
        schema = category_schema(category) if category else None
        if schema is None:
            return cleaned
        specs = normalize_specs(schema, cleaned.get("specs") or {})
        instance = self.instance
        # Older values stay editable within the listing's own category, as in
        # the dashboard; a move to another category starts from its rules.
        existing = (
            instance.specs
            if instance.pk and instance.category_id == category.id
            else {}
        )
        errors = validate_specs(
            schema,
            specs,
            condition=cleaned.get("condition") or ProductCondition.NEW,
            enforce_required=cleaned.get("status") != ProductStatus.DRAFT,
            existing=existing or {},
        )
        if errors:
            raise forms.ValidationError(
                {
                    "specs": [
                        f"{key}: {' '.join(messages)}"
                        for key, messages in sorted(errors.items())
                    ]
                }
            )
        cleaned["specs"] = specs
        return cleaned


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    form = ProductAdminForm
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
