"""Inline keyboard builders for Telegram bot interactions.

Pure constructors: no database queries, no ORM, no network I/O.
"""

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from bot.parsing import (
    format_digest_all_callback,
    format_digest_review_callback,
    format_listing_callback,
    format_numeric_selection_callback,
    format_page_callback,
    format_prompt_callback,
    format_review_action_callback,
    format_unlink_callback,
)

# Telegram truncates long button labels unhelpfully; do it ourselves so the
# product name stays readable.
BUTTON_LABEL_LIMIT = 55


def prompt_markup(request_id: int) -> dict:
    """Build inline keyboard payload for a confirmation prompt.

    Returned as a serializable dict so it can be stored directly in
    OutboundMessage.payload and JSON-serialized into the database.
    """
    return {
        "inline_keyboard": [
            [
                {
                    "text": "✅ Still available",
                    "callback_data": format_prompt_callback("confirm", request_id),
                },
                {
                    "text": "❌ Sold out",
                    "callback_data": format_prompt_callback("deny", request_id),
                },
            ]
        ]
    }


def listing_markup(
    products,
    page: int,
    pages: int,
    label_shop: bool = False,
) -> InlineKeyboardMarkup:
    """Build the inline keyboard markup for a /myproducts listing page."""
    rows = []
    for product in products:
        label = f"{product.brand} {product.name}"
        if label_shop:
            label = f"{product.shop.name} — {label}"
        if getattr(product, "is_available", True):
            rows.append(
                [
                    InlineKeyboardButton(
                        f"✅ {label}"[:BUTTON_LABEL_LIMIT],
                        callback_data=format_listing_callback("pconfirm", product.pk, page),
                    ),
                    InlineKeyboardButton(
                        "❌",
                        callback_data=format_listing_callback("pdeny", product.pk, page),
                    ),
                ]
            )
        else:
            rows.append(
                [
                    InlineKeyboardButton(
                        f"🔄 {label}"[:BUTTON_LABEL_LIMIT],
                        callback_data=format_listing_callback("pback", product.pk, page),
                    ),
                ]
            )

    nav = []
    if page > 0:
        nav.append(
            InlineKeyboardButton(
                "◀ Prev",
                callback_data=format_page_callback(page - 1),
            )
        )
    if page < pages - 1:
        nav.append(
            InlineKeyboardButton(
                "Next ▶",
                callback_data=format_page_callback(page + 1),
            )
        )
    if nav:
        rows.append(nav)

    return InlineKeyboardMarkup(rows)


def unlink_confirmation_markup() -> InlineKeyboardMarkup:
    """Build inline keyboard markup for confirming /unlink."""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "Yes, unlink",
                    callback_data=format_unlink_callback("yes"),
                ),
                InlineKeyboardButton(
                    "Cancel",
                    callback_data=format_unlink_callback("cancel"),
                ),
            ]
        ]
    )


def digest_markup(digest_id: int) -> InlineKeyboardMarkup:
    """Build inline keyboard markup for a digest."""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "✅ All still available",
                    callback_data=format_digest_all_callback(digest_id),
                )
            ],
            [
                InlineKeyboardButton(
                    "✏️ Check one by one",
                    callback_data=format_digest_review_callback(digest_id),
                )
            ],
        ]
    )


def review_markup(request_id: int) -> InlineKeyboardMarkup:
    """Build inline keyboard markup for stepping through review mode."""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "✅ Available",
                    callback_data=format_review_action_callback(request_id, "y"),
                ),
                InlineKeyboardButton(
                    "❌ Sold",
                    callback_data=format_review_action_callback(request_id, "n"),
                ),
            ],
            [
                InlineKeyboardButton(
                    "⏭ Skip",
                    callback_data=format_review_action_callback(request_id, "s"),
                ),
                InlineKeyboardButton(
                    "⏹ Stop",
                    callback_data=format_review_action_callback(request_id, "x"),
                ),
            ],
        ]
    )


def digest_ambiguous_markup(digest_id: int, indices: list[int]) -> InlineKeyboardMarkup:
    """Build inline keyboard markup for ambiguous numeric reply."""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "✅ Still available",
                    callback_data=format_numeric_selection_callback(
                        digest_id, "a", indices
                    ),
                ),
                InlineKeyboardButton(
                    "❌ Sold out",
                    callback_data=format_numeric_selection_callback(
                        digest_id, "s", indices
                    ),
                ),
            ]
        ]
    )
