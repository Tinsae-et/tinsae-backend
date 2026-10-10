"""Category spec schemas: what the listing form renders and what a save must hold.

`category_schema()` turns a category's product type into the payload served by
`GET /api/v1/catalog/categories/<slug>/schema/`. `validate_specs()` checks a
listing against that same payload, so the form and the API cannot disagree
about a rule -- the frontend runs the identical checks on the identical shape.
"""

import re
from decimal import Decimal

from django.db.models import Prefetch

from .models import AttributeOption, AttributeType, Category, ProductTypeAttribute
from .taxonomy import PRODUCT_TYPES

# Free-text keys the form writes outside the schema.
ALWAYS_ALLOWED_KEYS = {"variant"}
TEXT_MAX_LENGTH = 120


def _number(value):
    return float(value) if isinstance(value, Decimal) else value


def size_to_gb(value):
    """'256gb' / '512GB SSD' / '1tb' / '512MB' / 64 -> a number of GB, or None.

    Storage and RAM are stored in GB; this is the one parser for sizes written
    as text, used by the taxonomy conversion, `migrate_specs` and the seeds.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return value
    match = re.search(r"(\d+(?:\.\d+)?)\s*(tb|gb|mb)?", str(value), re.IGNORECASE)
    if not match:
        return None
    number = float(match.group(1))
    unit = (match.group(2) or "gb").lower()
    gb = number * 1024 if unit == "tb" else number / 1024 if unit == "mb" else number
    return int(gb) if float(gb).is_integer() else round(gb, 2)


def category_schema(category: Category | None) -> dict | None:
    """The schema payload for a category, or None when it has no product type."""
    if category is None or category.product_type_id is None:
        return None
    product_type = category.product_type
    links = (
        ProductTypeAttribute.objects.filter(product_type=product_type)
        .select_related("attribute")
        .prefetch_related(
            Prefetch(
                "attribute__options",
                queryset=AttributeOption.objects.filter(is_active=True),
            )
        )
        .order_by("display_order")
    )

    fields = []
    for link in links:
        attribute = link.attribute
        field = {
            "key": attribute.key,
            "label": link.label or attribute.label,
            "type": attribute.type,
            "unit": attribute.unit or None,
            "min": _number(attribute.min_value),
            "max": _number(attribute.max_value),
            "placeholder": link.placeholder or None,
            "hint": link.hint or None,
            "required": link.required,
            "required_if": link.required_if or None,
            "show_if": link.show_if or None,
            "control": link.control or None,
            "section": link.section,
            "recommended": link.recommended,
            "options": None,
        }
        # Enum: the closed list. Text: suggestions to type from (processor).
        options = attribute.options.all()
        if attribute.type != AttributeType.NUMBER and options:
            allowed = set(link.options or [])
            field["options"] = [
                {"value": o.value, "label": o.label}
                for o in options
                if not allowed or o.value in allowed
            ]
        fields.append(field)

    return {
        "category": category.slug,
        "product_type": product_type.key,
        "brands": list(product_type.brands or []),
        "brand_required": product_type.brand_required,
        # Form copy, read from the taxonomy code rather than the database: it
        # is presentation, not a rule, so it needs no sync.
        "listing_examples": PRODUCT_TYPES.get(product_type.key, {}).get(
            "listing_examples", {}
        ),
        "fields": fields,
    }


def as_text(field: dict, value):
    """For a text field, an option value becomes its label: 'apple_m3' -> 'Apple M3'.

    Text fields store what was typed, and a suggestion is typed as its label.
    Values that are not option values pass through unchanged.
    """
    if field["type"] != AttributeType.TEXT or not field["options"]:
        return value
    return next((o["label"] for o in field["options"] if o["value"] == value), value)


def normalize_specs(schema: dict, specs):
    """Tidy typed values before they are validated and stored.

    Text is trimmed and inner whitespace collapsed; a pick-or-type value that
    matches a suggestion by label or value, ignoring case, becomes that
    suggestion's label -- so "black", "BLACK " and "Black" are one colour to a
    filter, while "Titanium Blue" is kept exactly as typed.
    """
    if not isinstance(specs, dict):
        return specs
    out = dict(specs)
    for field in schema["fields"]:
        value = out.get(field["key"])
        if field["type"] != AttributeType.TEXT or not isinstance(value, str):
            continue
        value = " ".join(value.split())
        folded = value.casefold()
        for option in field["options"] or []:
            if folded in (option["label"].casefold(), option["value"].casefold()):
                value = option["label"]
                break
        out[field["key"]] = value
    return out


def matches_condition(condition: dict | None, values: dict) -> bool:
    """Whether every key in a `show_if` / `required_if` holds an allowed value."""
    if not condition:
        return True
    return all(
        str(values.get(key, "")) in allowed for key, allowed in condition.items()
    )


def validate_specs(
    schema: dict,
    specs,
    *,
    condition: str,
    enforce_required: bool = True,
    existing: dict | None = None,
) -> dict[str, list[str]]:
    """Errors keyed by spec key; empty when `specs` satisfies `schema`.

    `existing` is the listing's current specs. Keys already on it that the
    schema does not define are accepted, and so are unchanged values of keys it
    does define -- listings created before the schema must stay editable until
    `migrate_specs` has converted them. Anything new must follow the schema.

    `enforce_required=False` checks values but not completeness; drafts use it.
    """
    if not isinstance(specs, dict):
        return {"non_field_errors": ["Specs must be an object."]}
    existing = existing or {}
    values = {**specs, "condition": condition}
    errors: dict[str, list[str]] = {}
    defined = {f["key"]: f for f in schema["fields"]}

    for key in specs:
        if key in ALWAYS_ALLOWED_KEYS or key in defined:
            continue
        if key in existing:
            continue
        errors[key] = [f"Unknown spec for {schema['product_type']}."]

    for key, field in defined.items():
        visible = matches_condition(field["show_if"], values)
        present = key in specs and specs[key] not in (None, "")
        unchanged = present and key in existing and existing[key] == specs[key]

        if not visible:
            if present and not unchanged:
                errors[key] = [f"{field['label']} does not apply here."]
            continue

        if not present:
            required = field["required"] or (
                field["required_if"] is not None
                and matches_condition(field["required_if"], values)
            )
            if required and enforce_required:
                errors[key] = [f"{field['label']} is required."]
            continue

        if unchanged:
            continue
        message = check_value(field, specs[key])
        if message:
            errors[key] = [message]

    return errors


def check_value(field: dict, value) -> str | None:
    """The error message for one value against its field, or None when valid."""
    label = field["label"]
    if field["type"] == AttributeType.ENUM:
        allowed = {o["value"] for o in field["options"] or []}
        if value not in allowed:
            return f"{label}: choose one of {', '.join(sorted(allowed))}."
        return None
    if field["type"] == AttributeType.NUMBER:
        # JSON numbers only; bool is an int subclass and must not pass.
        if isinstance(value, bool) or not isinstance(value, int | float):
            return f"{label} must be a number."
        if field["min"] is not None and value < field["min"]:
            return f"{label} must be at least {field['min']:n}."
        if field["max"] is not None and value > field["max"]:
            return f"{label} must be at most {field['max']:n}."
        return None
    if not isinstance(value, str):
        return f"{label} must be text."
    if len(value) > TEXT_MAX_LENGTH:
        return f"{label} must be {TEXT_MAX_LENGTH} characters or fewer."
    return None
