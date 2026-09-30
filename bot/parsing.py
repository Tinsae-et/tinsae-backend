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


DIGEST_ALL_EXTRA = {
    "all",
    "everything",
    "all available",
    "all of them",
    "all still available",
    "ሁሉም",
    "ሁሉም አሉ",
}


def parse_digest_all(text: str) -> bool:
    """Read a free-text message as a confirmation of all items in a digest."""
    if not text:
        return False
    normalised = " ".join(_PUNCTUATION.sub(" ", text.strip().lower()).split())
    if normalised in DIGEST_ALL_EXTRA:
        return True
    if normalised in AFFIRMATIVE:
        return True
    return False


ALLOWED_SOLD_WORDS = {"sold", "soldout", "out", "and", "of", "stock", "የለም"}
SOLD_MARKERS = {"sold", "soldout", "የለም", "out"}


def parse_sold_indices(
    text: str,
) -> tuple[str | None, list[int]]:
    """Extract 1-based integer indices and classification ("sold" or "numbers").

    Returns ("sold" | "numbers" | None, list[int]).
    Disregards punctuation, dedupes preserving order, ignores indices > 50 or < 1.
    """
    if not text:
        return None, []

    cleaned = _PUNCTUATION.sub(" ", text.strip().lower())
    tokens = cleaned.split()
    if not tokens:
        return None, []

    seen = set()
    indices: list[int] = []
    words: list[str] = []

    for token in tokens:
        if token.isdigit():
            val = int(token)
            if 1 <= val <= 50 and val not in seen:
                seen.add(val)
                indices.append(val)
        else:
            words.append(token)

    if not indices:
        return None, []

    if not words:
        return "numbers", indices

    if set(words).issubset(ALLOWED_SOLD_WORDS) and any(
        w in SOLD_MARKERS for w in words
    ):
        return "sold", indices

    return None, []


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


# --- Callback family 6: Digest review start (drev:<digest_id>) ---


def format_digest_review_callback(digest_id: int) -> str:
    """Format callback_data for the digest 'Check one by one' button."""
    return f"drev:{digest_id}"


def parse_digest_review_callback(callback_data: str) -> int | None:
    """Parse drev:<digest_id> callback_data.

    Returns the digest_id (int) or None if malformed.
    """
    if not callback_data:
        return None
    prefix, sep, raw_id = callback_data.partition(":")
    if prefix == "drev" and raw_id.isdigit():
        return int(raw_id)
    return None


# --- Callback family 7: Review action (cr:<request_id>:<action>) ---


def format_review_action_callback(request_id: int, action: str) -> str:
    """Format callback_data for one-by-one review action buttons."""
    return f"cr:{request_id}:{action}"


def parse_review_action_callback(callback_data: str) -> tuple[int, str] | None:
    """Parse cr:<request_id>:<action> callback_data where action in y|n|s|x.

    Returns (request_id, action) or None if malformed.
    """
    if not callback_data:
        return None
    parts = callback_data.split(":")
    if (
        len(parts) == 3
        and parts[0] == "cr"
        and parts[1].isdigit()
        and parts[2] in ("y", "n", "s", "x")
    ):
        return int(parts[1]), parts[2]
    return None


# --- Callback family 8: Digest numeric selection (ni:<digest_id>:<a|s>:<i.j.k...>) ---


def format_numeric_selection_callback(
    digest_id: int, action: str, indices: list[int]
) -> str:
    """Format callback_data for ambiguous numeric reply buttons.

    action: 'a' (available) or 's' (sold).
    """
    joined = ".".join(str(i) for i in indices)
    return f"ni:{digest_id}:{action}:{joined}"


def parse_numeric_selection_callback(
    callback_data: str,
) -> tuple[int, str, list[int]] | None:
    """Parse ni:<digest_id>:<a|s>:<i.j.k...> callback_data.

    Returns (digest_id, action, indices) or None if malformed.
    """
    if not callback_data:
        return None
    parts = callback_data.split(":")
    if (
        len(parts) == 4
        and parts[0] == "ni"
        and parts[1].isdigit()
        and parts[2] in ("a", "s")
    ):
        raw_indices = parts[3].split(".")
        if raw_indices and all(idx.isdigit() for idx in raw_indices):
            return int(parts[1]), parts[2], [int(idx) for idx in raw_indices]
    return None
