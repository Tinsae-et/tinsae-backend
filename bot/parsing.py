"""Pure parsing utilities for Telegram bot input and callbacks.

No database, no Django models, no network I/O. Unit-testable without fixtures.
"""

import re

_PUNCTUATION = re.compile(r"[^\w\s]", re.UNICODE)

AFFIRMATIVE = {
    "yes",
    "y",
    "yeah",
    "yep",
    "yup",
    "ok",
    "okay",
    "sure",
    "available",
    "still available",
    "in stock",
    "still in stock",
    "have",
    "still have",
    "we have it",
    "አለ",
}

NEGATIVE = {
    "no",
    "n",
    "nope",
    "sold",
    "sold out",
    "soldout",
    "not available",
    "out of stock",
    "finished",
    "gone",
    "የለም",
}


def parse_answer(text: str) -> bool | None:
    """Read a free-text answer as yes/no, or None if it isn't one."""
    if not text:
        return None
    normalised = " ".join(_PUNCTUATION.sub(" ", text.strip().lower()).split())
    if normalised in AFFIRMATIVE:
        return True
    if normalised in NEGATIVE:
        return False
    return None


# --- Callback family 1: Prompt confirm/deny (confirm_<pk> / deny_<pk>) ---


def format_prompt_callback(action: str, request_id: int) -> str:
    """Format callback_data for confirmation prompt buttons."""
    return f"{action}_{request_id}"


def parse_prompt_callback(callback_data: str) -> tuple[str, int] | None:
    """Parse confirm_<pk> or deny_<pk> callback_data.

    Returns (action, request_id) or None if malformed.
    """
    action, sep, raw_id = callback_data.partition("_")
    if action in ("confirm", "deny") and raw_id.isdigit():
        return action, int(raw_id)
    return None


# --- Callback family 2: Listing confirm/deny (pconfirm:<pk>:<page> / pdeny:<pk>:<page>) ---


def format_listing_callback(action: str, product_id: int, page: int) -> str:
    """Format callback_data for /myproducts action buttons."""
    return f"{action}:{product_id}:{page}"


def parse_listing_callback(callback_data: str) -> tuple[str, int, int] | None:
    """Parse pconfirm:<pk>:<page> or pdeny:<pk>:<page> callback_data.

    Returns (action, product_id, page) or None if malformed.
    """
    parts = callback_data.split(":")
    if len(parts) >= 2 and parts[0] in ("pconfirm", "pdeny") and parts[1].isdigit():
        page = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
        return parts[0], int(parts[1]), page
    return None


# --- Callback family 3: Listing pagination (page:<n>) ---


def format_page_callback(page: int) -> str:
    """Format callback_data for /myproducts pagination buttons."""
    return f"page:{page}"


def parse_page_callback(callback_data: str) -> int | None:
    """Parse page:<n> callback_data.

    Returns page index (int) or None if malformed.
    """
    parts = callback_data.split(":")
    if len(parts) == 2 and parts[0] == "page" and parts[1].isdigit():
        return int(parts[1])
    return None


# --- Callback family 4: Unlink confirmation (unlink:<action>) ---


def format_unlink_callback(action: str = "yes") -> str:
    """Format callback_data for /unlink confirmation buttons."""
    return f"unlink:{action}"


def parse_unlink_callback(callback_data: str) -> str | None:
    """Parse unlink:<action> callback_data.

    Returns the action ('yes' or 'cancel') or None if malformed.
    """
    parts = callback_data.split(":")
    if len(parts) == 2 and parts[0] == "unlink" and parts[1] in ("yes", "cancel"):
        return parts[1]
    return None


# --- Callback family 5: Digest confirm all (da:<digest_id>) ---


def format_digest_all_callback(digest_id: int) -> str:
    """Format callback_data for the digest 'All still available' button."""
    return f"da:{digest_id}"


def parse_digest_all_callback(callback_data: str) -> int | None:
    """Parse da:<digest_id> callback_data.

    Returns the digest_id (int) or None if malformed.
    """
    if not callback_data:
        return None
    prefix, sep, raw_id = callback_data.partition(":")
    if prefix == "da" and raw_id.isdigit():
        return int(raw_id)
    return None
