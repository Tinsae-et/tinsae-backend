"""The product taxonomy, as code.

Attributes, their options, product types and which category uses which type
are defined in the modules here and written to the database by
`manage.py sync_taxonomy`. Changing a form or a validation rule is a reviewed
code change; Django admin shows these models read-only.

Rules the sync enforces, because listings already store these keys:
- an attribute's `key` and `type` never change once created;
- an option dropped from the code is deactivated, never deleted.
"""

from . import electronics, home, vehicles


def _merge(*parts):
    """One dict from several modules; a key defined twice is a mistake."""
    merged = {}
    for part in parts:
        overlap = merged.keys() & part.keys()
        if overlap:
            raise ValueError(f"Taxonomy keys defined twice: {sorted(overlap)}")
        merged.update(part)
    return merged


ATTRIBUTES = _merge(electronics.ATTRIBUTES, vehicles.ATTRIBUTES, home.ATTRIBUTES)
PRODUCT_TYPES = _merge(
    electronics.PRODUCT_TYPES, vehicles.PRODUCT_TYPES, home.PRODUCT_TYPES
)

__all__ = ["ATTRIBUTES", "PRODUCT_TYPES"]
