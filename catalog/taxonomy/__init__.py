"""The product taxonomy, as code.

Attributes, their options, product types and which category uses which type
are defined in the modules here and written to the database by
`manage.py sync_taxonomy`. Changing a form or a validation rule is a reviewed
code change; Django admin shows these models read-only.

Rules the sync enforces, because listings already store these keys:
- an attribute's `key` and `type` never change once created;
- an option dropped from the code is deactivated, never deleted.
"""

from .electronics import ATTRIBUTES, PRODUCT_TYPES

__all__ = ["ATTRIBUTES", "PRODUCT_TYPES"]
