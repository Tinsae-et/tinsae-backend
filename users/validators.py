"""Canonicalisation for the identity fields.

Both helpers are total over "whatever was in the field": they take the raw value
and return either a canonical value or ``None``. ``None`` -- not ``""`` -- is the
empty case, because both columns are ``unique=True`` and Postgres treats NULLs as
distinct while it treats two empty strings as a collision. Stored as ``""``,
exactly one user in the table could have no phone.

Stdlib only, per `.agents/AGENTS.md` §2 -- no `phonenumbers` dependency.
"""

import re

from django.core.exceptions import ValidationError

# Ethiopian mobile numbers are a 9-digit national significant number beginning
# with 9 (Ethio Telecom) or 7 (Safaricom Ethiopia). Landlines (11, 22, 33, ...)
# are deliberately not accepted: this field is a login identifier and the
# fallback contact for a merchant, so it has to be a number a person carries.
_ETHIOPIAN_MOBILE = re.compile(r"^[79]\d{8}$")

# Everything a human plausibly types between the digits.
_SEPARATORS = re.compile(r"[\s\-().]")


def normalize_phone(value):
    """Return an Ethiopian mobile number as E.164 (`+2519XXXXXXXX`), or None.

    Accepts the four forms merchants actually type -- `0911234567`,
    `911234567`, `251911234567`, `+251911234567` -- with any spacing or dashes.
    Anything else raises, so a junk phone is rejected where it is entered rather
    than stored and discovered later by the bot.
    """
    if value is None:
        return None

    cleaned = _SEPARATORS.sub("", str(value)).strip()
    if not cleaned:
        return None

    if cleaned.startswith("+251"):
        national = cleaned[4:]
    elif cleaned.startswith("251"):
        national = cleaned[3:]
    elif cleaned.startswith("0"):
        national = cleaned[1:]
    else:
        national = cleaned

    if not _ETHIOPIAN_MOBILE.match(national):
        raise ValidationError(
            "Enter a valid Ethiopian mobile number, e.g. 0911234567 or "
            "+251911234567.",
            code="invalid_phone",
        )

    return f"+251{national}"


def looks_like_phone(value):
    """True if `value` would be routed to the phone column at login.

    Registration uses this to keep merchants from choosing a username that the
    login backend can never reach -- see `users.backends` for why routing is by
    shape.
    """
    try:
        return normalize_phone(value) is not None
    except ValidationError:
        return False


def normalize_email(value):
    """Lowercase and strip an email, or return None if there isn't one.

    Django's `BaseUserManager.normalize_email` only lowercases the *domain*,
    which leaves `Sara@x.com` and `sara@x.com` as two distinct rows under a
    unique constraint. Mail servers treat the local part as case-sensitive in
    theory and never in practice, so full lowercasing is what makes uniqueness
    mean what users assume it means.
    """
    if value is None:
        return None
    cleaned = str(value).strip().lower()
    return cleaned or None
