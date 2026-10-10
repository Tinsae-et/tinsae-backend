"""Write the taxonomy definitions to the database.

Idempotent: a second run with unchanged definitions reports no changes. Runs in
one transaction, so a definition error leaves the database as it was.
"""

from decimal import Decimal

from django.db import transaction

from catalog.models import (
    Attribute,
    AttributeOption,
    Category,
    Product,
    ProductType,
    ProductTypeAttribute,
)
from catalog.specs import size_to_gb


class TaxonomyError(Exception):
    """A definition the sync refuses to apply."""


# Keys a `show_if` / `required_if` may read besides the type's own attributes.
PRODUCT_FIELDS = {"condition"}

# Where a field sits in the form and on the product page.
SECTIONS = {"key", "details", "item"}

# The only type changes allowed: ones that keep every stored value valid. Any
# enum value is also valid text, so a closed list may open up (processor went
# this way); nothing may narrow, and nothing becomes a number.
WIDENING_TYPE_CHANGES = {("enum", "text")}


def _decimal(value):
    return None if value is None else Decimal(str(value))


# Named conversions an attribute may declare with `convert_from`, for a type
# change that is not a pure widening. Each maps one stored value to the new
# type, or None when it cannot.
CONVERTERS = {"size_gb": size_to_gb}


def convert_stored_values(key: str, convert) -> tuple[int, int]:
    """Rewrite every stored value of `key` with `convert`; runs in the sync's
    transaction. A value that cannot be converted moves to `<key>_text`, where
    it stays visible as a legacy spec, and the listing asks for a new value.
    """
    converted = moved = 0
    batch = []
    products = Product.objects.filter(specs__has_key=key).only("id", "specs")
    for product in products.iterator(chunk_size=500):
        specs = dict(product.specs)
        new = convert(specs[key])
        if new is None:
            specs[f"{key}_text"] = specs.pop(key)
            moved += 1
        else:
            specs[key] = new
            converted += 1
        product.specs = specs
        batch.append(product)
        if len(batch) >= 500:
            Product.objects.bulk_update(batch, ["specs"])
            batch = []
    if batch:
        Product.objects.bulk_update(batch, ["specs"])
    return converted, moved


def check_definitions(attributes: dict, product_types: dict) -> None:
    """Reject definitions that would produce a broken form, before touching the DB."""
    for key, spec in attributes.items():
        if spec["type"] == "enum" and not spec.get("options"):
            raise TaxonomyError(f"Enum attribute {key!r} has no options.")
        # Text attributes may carry options: suggestions, not a closed list.
        if spec["type"] == "number" and spec.get("options"):
            raise TaxonomyError(f"Number attribute {key!r} cannot have options.")
        conversion = spec.get("convert_from")
        if conversion and conversion.get("using") not in CONVERTERS:
            raise TaxonomyError(
                f"Attribute {key!r} converts with unknown {conversion.get('using')!r}."
            )

    for type_key, spec in product_types.items():
        own_keys = {field["attribute"] for field in spec["fields"]}
        for field in spec["fields"]:
            attr_key = field["attribute"]
            if attr_key not in attributes:
                raise TaxonomyError(f"{type_key}: unknown attribute {attr_key!r}.")
            known_values = {o["value"] for o in attributes[attr_key].get("options", [])}
            unknown = set(field.get("options", [])) - known_values
            if unknown:
                raise TaxonomyError(
                    f"{type_key}.{attr_key}: options not defined on the attribute: "
                    f"{sorted(unknown)}."
                )
            if field.get("section", "key") not in SECTIONS:
                raise TaxonomyError(
                    f"{type_key}.{attr_key}: section must be one of {sorted(SECTIONS)}."
                )
            if field.get("recommended") and field.get("required"):
                raise TaxonomyError(
                    f"{type_key}.{attr_key}: a field is required or recommended, "
                    "not both."
                )
            for rule in ("show_if", "required_if"):
                for dep in (field.get(rule) or {}).keys():
                    if dep not in own_keys | PRODUCT_FIELDS:
                        raise TaxonomyError(
                            f"{type_key}.{attr_key}: {rule} reads {dep!r}, which is "
                            "not a field of this type."
                        )


def _set(obj, changes: list, what: str, /, **values) -> bool:
    """Assign changed values; record what changed. Returns whether anything did.

    Positional-only, because the field values include `label` and must never
    collide with this function's own parameters.
    """
    changed = [name for name, value in values.items() if getattr(obj, name) != value]
    for name in changed:
        setattr(obj, name, values[name])
    if changed:
        changes.append(f"update {what}: {', '.join(changed)}")
    return bool(changed)


@transaction.atomic
def sync_taxonomy(attributes: dict, product_types: dict, *, dry_run=False) -> list:
    """Apply the definitions; return a human-readable list of changes."""
    check_definitions(attributes, product_types)
    changes: list[str] = []

    # --- attributes and their options ---
    attribute_rows = {}
    for key, spec in attributes.items():
        values = {
            "label": spec["label"],
            "unit": spec.get("unit", ""),
            "min_value": _decimal(spec.get("min")),
            "max_value": _decimal(spec.get("max")),
        }
        attribute = Attribute.objects.filter(key=key).first()
        if attribute is None:
            attribute = Attribute.objects.create(key=key, type=spec["type"], **values)
            changes.append(f"create attribute {key}")
        else:
            # A declared type conversion rewrites every stored value in this
            # same transaction, so a unit may change along with it.
            retyped = attribute.type != spec["type"]
            if retyped:
                conversion = spec.get("convert_from") or {}
                if conversion.get("type") == attribute.type:
                    converted, moved = convert_stored_values(
                        key, CONVERTERS[conversion["using"]]
                    )
                    changes.append(
                        f"convert attribute {key}: {attribute.type} -> {spec['type']} "
                        f"({converted} value(s) converted, {moved} unreadable "
                        f"moved to {key}_text)"
                    )
                elif (attribute.type, spec["type"]) in WIDENING_TYPE_CHANGES:
                    changes.append(
                        f"widen attribute {key}: {attribute.type} -> {spec['type']}"
                    )
                else:
                    raise TaxonomyError(
                        f"Attribute {key!r} is {attribute.type} in the database and "
                        f"{spec['type']} in code. Types never change on their own: "
                        "declare a `convert_from` or add a new key."
                    )
                attribute.type = spec["type"]
                attribute.save(update_fields=["type", "updated_at"])
            if (
                not retyped
                and attribute.unit != values["unit"]
                and Product.objects.filter(specs__has_key=key).exists()
            ):
                # Stored numbers carry no unit of their own: 1200 saved as
                # watts would silently read as 1200 kW. Like a type change, a
                # unit change needs a new key while values exist.
                raise TaxonomyError(
                    f"Attribute {key!r} is in {attribute.unit or 'no unit'} in the "
                    f"database and {values['unit'] or 'no unit'} in code, and "
                    "listings hold values for it. Add a new key instead."
                )
            if _set(attribute, changes, f"attribute {key}", **values):
                attribute.save()
        attribute_rows[key] = attribute

        wanted = {
            o["value"]: (i, o["label"]) for i, o in enumerate(spec.get("options", []))
        }
        for option in attribute.options.all():
            if option.value in wanted:
                order, label = wanted.pop(option.value)
                if _set(
                    option,
                    changes,
                    f"option {key}={option.value}",
                    label=label,
                    display_order=order,
                    is_active=True,
                ):
                    option.save()
            elif option.is_active:
                option.is_active = False
                option.save()
                changes.append(f"deactivate option {key}={option.value}")
        for value, (order, label) in wanted.items():
            AttributeOption.objects.create(
                attribute=attribute, value=value, label=label, display_order=order
            )
            changes.append(f"create option {key}={value}")

    extra = Attribute.objects.exclude(key__in=attributes).values_list("key", flat=True)
    for key in extra:
        changes.append(f"note: attribute {key} is in the database but not in code")

    # --- product types, their fields, and category links ---
    for type_key, spec in product_types.items():
        product_type = ProductType.objects.filter(key=type_key).first()
        if product_type is None:
            product_type = ProductType.objects.create(
                key=type_key,
                label=spec["label"],
                brands=spec.get("brands", []),
                brand_required=spec.get("brand_required", True),
            )
            changes.append(f"create product type {type_key}")
        elif _set(
            product_type,
            changes,
            f"product type {type_key}",
            label=spec["label"],
            brands=spec.get("brands", []),
            brand_required=spec.get("brand_required", True),
        ):
            product_type.save()

        existing = {
            link.attribute.key: link
            for link in product_type.fields.select_related("attribute")
        }
        for order, field in enumerate(spec["fields"]):
            attr_key = field["attribute"]
            values = {
                "label": field.get("label", ""),
                "required": field.get("required", False),
                "required_if": field.get("required_if"),
                "show_if": field.get("show_if"),
                "options": field.get("options", []),
                "control": field.get("control", ""),
                "section": field.get("section", "key"),
                "recommended": field.get("recommended", False),
                "placeholder": field.get("placeholder", ""),
                "hint": field.get("hint", ""),
                "display_order": order,
            }
            link = existing.pop(attr_key, None)
            if link is None:
                ProductTypeAttribute.objects.create(
                    product_type=product_type,
                    attribute=attribute_rows[attr_key],
                    **values,
                )
                changes.append(f"add field {type_key}.{attr_key}")
            elif _set(link, changes, f"field {type_key}.{attr_key}", **values):
                link.save()
        # A field dropped from a type is unlinked; listings keep the stored value
        # as a legacy key, which validation still accepts on those listings.
        for attr_key, link in existing.items():
            link.delete()
            changes.append(f"remove field {type_key}.{attr_key}")

        for slug in spec.get("categories", []):
            category = Category.objects.filter(slug=slug).first()
            if category is None:
                changes.append(f"note: category {slug} does not exist; not linked")
            elif category.product_type_id != product_type.id:
                category.product_type = product_type
                category.save(update_fields=["product_type", "updated_at"])
                changes.append(f"link category {slug} -> {type_key}")

    if dry_run:
        transaction.set_rollback(True)
    return changes
