"""Product list filters beyond price and brand: condition, and a category's specs.

Spec filters are driven by the category's schema (catalog/specs.py), so every
category gets the filters its listings actually carry, with no per-category
code. They are query params prefixed `spec_`:

    ?category=cars&spec_fuel=diesel,petrol     an option, or any of several
    &spec_year_min=2015&spec_year_max=2020     a number range, either end open
    &spec_color=White                          text: case-insensitive match

Values of one spec are ORed; different specs (and every other filter) are
ANDed. Only keys the category's schema defines are accepted, so a typo is a
400 rather than a filter that silently matches everything.

`value_counts` and `number_range` feed the facets endpoint: how many listings
each option would show, so the filter rail can print real counts.
"""

import math
from collections import Counter

from django.db.models import Count, Q
from django.db.models.fields.json import KT
from rest_framework.exceptions import ValidationError

from .models import ProductCondition

SPEC_PREFIX = "spec_"
# A text spec (colour, processor...) can hold many distinct typed values; the
# rail shows the most common ones.
MAX_FACET_VALUES = 12


def list_param(params, name) -> list[str]:
    """A comma-separated query param as a list of non-empty values."""
    return [v.strip() for v in (params.get(name) or "").split(",") if v.strip()]


def condition_filter(params) -> Q | None:
    values = list_param(params, "condition")
    if not values:
        return None
    unknown = sorted(set(values) - set(ProductCondition.values))
    if unknown:
        raise ValidationError(
            {"condition": f"Unknown condition: {', '.join(unknown)}."}
        )
    return Q(condition__in=values)


def _number(name, raw):
    try:
        value = float(raw)
    except ValueError:
        value = math.nan
    if not math.isfinite(value):
        raise ValidationError({name: f"Not a number: {raw}."})
    return value


def parse_spec_params(params) -> dict[str, dict]:
    """`spec_*` params grouped by spec key: {"values": [...]} or {"min", "max"}."""
    wanted: dict[str, dict] = {}
    for name in params:
        if not name.startswith(SPEC_PREFIX):
            continue
        key = name[len(SPEC_PREFIX) :]
        for bound in ("min", "max"):
            if key.endswith(f"_{bound}"):
                spec = wanted.setdefault(key[: -len(bound) - 1], {})
                if params.get(name):
                    spec[bound] = _number(name, params.get(name))
                break
        else:
            values = list_param(params, name)
            if values:
                wanted.setdefault(key, {})["values"] = values
    return {key: spec for key, spec in wanted.items() if spec}


def spec_filters(params, schema: dict | None) -> dict[str, Q]:
    """One Q per filtered spec key, validated against the category's schema."""
    wanted = parse_spec_params(params)
    if not wanted:
        return {}
    if schema is None:
        raise ValidationError(
            {"category": "Spec filters need a category with specs, such as cars."}
        )
    fields = {f["key"]: f for f in schema["fields"]}
    filters = {}
    for key, spec in wanted.items():
        field = fields.get(key)
        if field is None:
            raise ValidationError(
                {f"{SPEC_PREFIX}{key}": f"Unknown spec for {schema['product_type']}."}
            )
        is_number = field["type"] == "number"
        if "values" in spec:
            if is_number:
                raise ValidationError(
                    {
                        f"{SPEC_PREFIX}{key}": (
                            f"{field['label']} is a number: use "
                            f"{SPEC_PREFIX}{key}_min / {SPEC_PREFIX}{key}_max."
                        )
                    }
                )
            if field.get("multiple"):
                # A list matches when it holds any picked value: "size M"
                # finds the shirt sold in S to XL. JSON containment, so it
                # is exact; the picked values come from the facet counts,
                # which are stored values.
                q = Q()
                for value in spec["values"]:
                    q |= Q(**{f"specs__{key}__contains": [value]})
                filters[key] = q
            elif field["type"] == "enum":
                filters[key] = Q(**{f"specs__{key}__in": spec["values"]})
            else:
                # Text is stored as typed (tidied to a suggestion's label), so
                # match it the way a person would: ignoring case.
                q = Q()
                for value in spec["values"]:
                    q |= Q(**{f"specs__{key}__iexact": value})
                filters[key] = q
        else:
            if not is_number:
                raise ValidationError(
                    {f"{SPEC_PREFIX}{key}": f"{field['label']} has no range."}
                )
            q = Q()
            if "min" in spec:
                q &= Q(**{f"specs__{key}__gte": spec["min"]})
            if "max" in spec:
                q &= Q(**{f"specs__{key}__lte": spec["max"]})
            filters[key] = q
    return filters


def value_counts(
    qs, key: str, *, multiple: bool = False, order: list[str] | None = None
) -> list[dict]:
    """The most common stored values of a spec, with how many listings hold each.

    For a multiple spec each value in a listing's list counts once: a shirt
    in S, M and L adds one to each size. Its values are listed in `order` (the
    field's options as stored), so sizes read S, M, L, XL rather than by count.
    """
    if multiple:
        return _list_value_counts(qs, key, order or [])
    rows = (
        qs.annotate(_value=KT(f"specs__{key}"))
        .exclude(_value__isnull=True)
        .exclude(_value="")
        .values("_value")
        .annotate(count=Count("id"))
        .order_by("-count", "_value")[:MAX_FACET_VALUES]
    )
    return [{"value": row["_value"], "count": row["count"]} for row in rows]


def _list_value_counts(qs, key: str, order: list[str]) -> list[dict]:
    """Per-item counts over list values, counted in Python.

    Postgres cannot group by the items of a JSON array without a lateral
    join; at the catalogue's size, reading the lists is cheaper to keep.
    A single (legacy) value counts as a one-item list.
    """
    counts: Counter = Counter()
    for value in qs.filter(**{f"specs__{key}__isnull": False}).values_list(
        f"specs__{key}", flat=True
    ):
        items = value if isinstance(value, list) else [value]
        counts.update({str(item) for item in items if item not in (None, "")})
    top = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:MAX_FACET_VALUES]
    position = {value: i for i, value in enumerate(order)}

    def in_order(item):
        # Options first, as listed; then typed values, numbers numerically
        # ("30" before "100"), then the rest alphabetically.
        value = item[0]
        if value in position:
            return (0, position[value], "")
        try:
            return (1, float(value), "")
        except ValueError:
            return (2, 0, value.casefold())

    return [{"value": v, "count": n} for v, n in sorted(top, key=in_order)]


def number_range(qs, key: str) -> dict | None:
    """Lowest and highest value of a number spec, or None when none is stored.

    The `gte` keeps JSON numbers only: Postgres orders every jsonb string below
    every number, so an unconverted legacy "256GB" falls out instead of
    breaking the comparison.
    """
    numeric = qs.filter(**{f"specs__{key}__gte": -1e15})
    path = f"specs__{key}"
    low = numeric.order_by(path).values_list(path, flat=True).first()
    if low is None:
        return None
    high = numeric.order_by(f"-{path}").values_list(path, flat=True).first()
    return {"min": low, "max": high}
