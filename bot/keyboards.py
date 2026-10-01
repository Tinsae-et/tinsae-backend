"""Inline keyboard builders for Telegram bot interactions.

Pure constructors: no database queries, no ORM, no network I/O.
"""

from django.conf import settings
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from bot.listing_states import LISTING_PAUSED, LISTING_SOLD
from bot.parsing import (
    format_digest_all_callback,
    format_digest_review_callback,
    format_listing_callback,
    format_menu_callback,
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
    """Build the inline keyboard markup for a /myproducts listing page.

    Buttons follow each product's state (`listing_rank`): due/fresh (live and
    available) offer Confirm · Sold · Pause; paused offers Resume · Sold; sold
    offers Back in stock.
    """
    rows = []
    for product in products:
        label = f"{product.brand} {product.name}"
        if label_shop:
            label = f"{product.shop.name} — {label}"
        rank = getattr(product, "listing_rank", None)
        if rank == LISTING_SOLD:
            rows.append(
                [
                    InlineKeyboardButton(
                        f"🔄 {label}"[:BUTTON_LABEL_LIMIT],
                        callback_data=format_listing_callback(
                            "pback", product.pk, page
                        ),
                    ),
                ]
            )
        elif rank == LISTING_PAUSED:
            rows.append(
                [
                    InlineKeyboardButton(
                        f"▶️ {label}"[:BUTTON_LABEL_LIMIT],
                        callback_data=format_listing_callback(
                            "presume", product.pk, page
                        ),
                    ),
                    InlineKeyboardButton(
                        "❌",
                        callback_data=format_listing_callback(
                            "pdeny", product.pk, page
                        ),
                    ),
                ]
            )
        else:
            # Live and available (due if rank 0, fresh otherwise).
            rows.append(
                [
                    InlineKeyboardButton(
                        f"✅ {label}"[:BUTTON_LABEL_LIMIT],
                        callback_data=format_listing_callback(
                            "pconfirm", product.pk, page
                        ),
                    ),
                    InlineKeyboardButton(
                        "❌",
                        callback_data=format_listing_callback(
                            "pdeny", product.pk, page
                        ),
                    ),
                    InlineKeyboardButton(
                        "⏸",
                        callback_data=format_listing_callback(
                            "ppause", product.pk, page
                        ),
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

    rows.append([_menu_button()])

    return InlineKeyboardMarkup(rows)


def _menu_button() -> InlineKeyboardButton:
    """The ☰ Menu button, shown on navigable messages so merchants can get back."""
    return InlineKeyboardButton(
        "☰ Menu",
        callback_data=format_menu_callback("home"),
    )


def _dashboard_url(override: str | None = None) -> str:
    """Return a Telegram-compliant public URL for the dashboard button.

    Telegram Bot API strictly rejects 'localhost', '127.0.0.1', and loopback hostnames
    with `BadRequest: wrong http url`. When FRONTEND_URL is localhost or not a public URL,
    we fall back to 'https://tinsae.et/dashboard' so keyboards render without crashing.
    """
    candidate = override or getattr(settings, "FRONTEND_URL", "")
    candidate = candidate.rstrip("/")
    if not candidate or "localhost" in candidate or "127.0.0.1" in candidate:
        return "https://tinsae.et/dashboard"
    if candidate.endswith("/dashboard"):
        return candidate
    return f"{candidate}/dashboard"


def menu_markup(dashboard_url: str | None = None) -> InlineKeyboardMarkup:
    """Build the /menu navigation hub."""
    target_url = _dashboard_url(dashboard_url)
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "🛒 My products",
                    callback_data=format_menu_callback("products"),
                ),
                InlineKeyboardButton(
                    "📊 Status",
                    callback_data=format_menu_callback("status"),
                ),
            ],
            [
                InlineKeyboardButton(
                    "🌐 Open Dashboard ↗",
                    url=target_url,
                ),
                InlineKeyboardButton(
                    "❓ Help",
                    callback_data=format_menu_callback("help"),
                ),
            ],
            [
                InlineKeyboardButton(
                    "🔌 Unlink",
                    callback_data=format_menu_callback("unlink"),
                ),
                InlineKeyboardButton(
                    "🏖 Vacation mode",
                    callback_data=format_menu_callback("vacation"),
                ),
            ],
        ]
    )


def help_markup(dashboard_url: str | None = None) -> InlineKeyboardMarkup:
    """Build inline keyboard markup for /help and menu help."""
    target_url = _dashboard_url(dashboard_url)
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("🌐 Open Dashboard ↗", url=target_url),
                _menu_button(),
            ]
        ]
    )


def vacation_menu_markup() -> InlineKeyboardMarkup:
    """Build inline keyboard markup for vacation mode menu."""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "⏸ Pause all listings",
                    callback_data=format_menu_callback("vac_pause"),
                ),
                InlineKeyboardButton(
                    "▶️ Resume all listings",
                    callback_data=format_menu_callback("vac_resume"),
                ),
            ],
            [_menu_button()],
        ]
    )


def vacation_paused_markup() -> InlineKeyboardMarkup:
    """Build markup after pausing listings in vacation mode."""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "▶️ Resume all listings",
                    callback_data=format_menu_callback("vac_resume"),
                )
            ],
            [_menu_button()],
        ]
    )


def vacation_resumed_markup() -> InlineKeyboardMarkup:
    """Build markup after resuming listings in vacation mode."""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "🛒 My products",
                    callback_data=format_menu_callback("products"),
                )
            ],
            [_menu_button()],
        ]
    )


def status_markup(
    total_due: int = 0,
    dashboard_url: str | None = None,
) -> InlineKeyboardMarkup:
    """Build the inline keyboard markup for /status."""
    target_url = _dashboard_url(dashboard_url)

    if total_due > 0:
        return InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        f"⚡ Review Due Items ({total_due})",
                        callback_data=format_menu_callback("products"),
                    )
                ],
                [
                    InlineKeyboardButton("🌐 Open Dashboard ↗", url=target_url),
                    _menu_button(),
                ],
            ]
        )
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "🛒 My products",
                    callback_data=format_menu_callback("products"),
                ),
                InlineKeyboardButton("🌐 Open Dashboard ↗", url=target_url),
            ],
            [_menu_button()],
        ]
    )


def undo_sold_markup(product_pk: int) -> InlineKeyboardMarkup:
    """Build keyboard offering an immediate Undo for a quick-sold product."""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "↩️ Undo (Back in stock)",
                    callback_data=format_listing_callback("pback", product_pk, 999),
                )
            ],
            [_menu_button()],
        ]
    )


def disambiguate_sold_markup(products) -> InlineKeyboardMarkup:
    """Build buttons to pick which product to mark sold when query is ambiguous."""
    rows = []
    for product in products:
        label = f"❌ {product.brand} {product.name}"[:BUTTON_LABEL_LIMIT]
        rows.append(
            [
                InlineKeyboardButton(
                    label,
                    callback_data=format_listing_callback("pdeny", product.pk, 999),
                )
            ]
        )
    rows.append([_menu_button()])
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
            [_menu_button()],
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
