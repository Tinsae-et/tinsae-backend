"""User-facing message strings for the Telegram bot.

Every merchant-facing string is defined here as a pure function returning plain
text. No Django model imports, no ORM, no Telegram dependencies, and no HTML
escaping (plain text v1; formatting is deferred per D21).
"""

import zoneinfo
from datetime import datetime
from decimal import Decimal

EAT = zoneinfo.ZoneInfo("Africa/Addis_Ababa")

# The commands advertised in Telegram's client menu (setMyCommands). Order is the
# order they appear. Kept here so run_bot and the sync_bot_commands command share
# one source.
BOT_COMMANDS = [
    ("start", "Start or connect your shop"),
    ("menu", "Open the menu"),
    ("myproducts", "View and manage your products"),
    ("status", "Listing freshness and last check-in"),
    ("pause_shop", "Pause all listings (vacation mode)"),
    ("resume_shop", "Resume all paused listings"),
    ("unlink", "Disconnect this chat from your shop"),
    ("link", "Connect using a shop code"),
    ("help", "How to use the bot"),
]


def menu() -> str:
    return "What would you like to do?"


def welcome(chat_id: str | int) -> str:
    return (
        "Welcome to Tinsae marketplace.\n\n"
        "To connect your shop, send me its link code — like this:\n"
        "/link TIN-ABC2345\n\n"
        "Ask us for a code if you don't have one. Enter /help for help.\n"
        f"(Support may ask for this chat ID: {chat_id})"
    )


def help_text() -> str:
    return (
        "Here is what you can do with this bot:\n\n"
        "Commands:\n"
        "/menu — open the interactive navigation hub\n"
        "/myproducts — browse, confirm, pause, or restock items\n"
        "/status — check listing freshness, due items, and check-in\n"
        "/pause_shop — vacation mode: pause all listings while away\n"
        "/resume_shop — resume all listings when you return\n"
        "/unlink — disconnect this chat from your shop\n"
        "/help — show this help guide\n\n"
        "Quick sold (anytime):\n"
        'Text "sold [item name]" (e.g. "sold iPhone 15") to mark an item out of stock immediately.\n'
        "The bot finds it and provides an instant 1-tap Undo button.\n\n"
        "Weekly check-in:\n"
        'Reply "all" if everything is still in stock.\n'
        'Reply "sold 2 5" to mark items 2 and 5 out of stock.\n\n'
        "Web Dashboard:\n"
        "Tap the Open Dashboard button in /menu or /status to manage prices, photos, and settings."
    )


def link_usage() -> str:
    return "Send the code along with the command, like this:\n/link TIN-ABC2345"


def link_ack(outcome: str, shop_name: str | None = None) -> str:
    """One short line per link outcome. Every failure names a way forward."""
    if outcome == "throttled":
        return "Too many attempts. Please wait a while and try again."
    if outcome == "linked":
        return (
            "✅ Linked! This chat now receives stock confirmations for "
            f"{shop_name}.\nSend /myproducts to see what's waiting on you."
        )
    if outcome == "relinked":
        return (
            f"✅ Done — {shop_name} now sends its confirmations here instead of "
            "the chat it used before.\nSend /myproducts to see what's waiting."
        )
    if outcome == "already_linked":
        return (
            f"👍 This chat is already linked to {shop_name}. "
            "Send /myproducts to see what's waiting on you."
        )
    if outcome == "expired":
        return (
            "That code has expired — they are only good for a short while. "
            "Ask us for a new one and send it here."
        )
    if outcome == "used":
        return (
            "That code has already been used. Ask us for a new one if this chat "
            "still needs linking to your shop."
        )
    return (
        "I don't recognise that code. They look like TIN-ABC2345 — check it and "
        "send it again, or ask us for a new one."
    )


def listing_summary(total: int, page: int, pages: int) -> str:
    noun = "product" if total == 1 else "products"
    return (
        f"Your {noun} — {total} (page {page + 1} of {pages}).\n"
        "Tap a button to update a product."
    )


def not_linked() -> str:
    return (
        "This chat is not linked to a shop yet. Send your shop's link code — "
        "like /link TIN-ABC2345 — or ask us for one."
    )


def no_products() -> str:
    return "You have no products yet. Add one from the dashboard."


def nothing_left_to_confirm() -> str:
    return "Nothing left to confirm. 🎉"


def could_not_record() -> str:
    return "Couldn't record that — send /myproducts for a fresh list."


def recorded() -> str:
    return "Recorded"


def request_inactive() -> str:
    return "This request is no longer active."


def response_recorded() -> str:
    return "Response recorded"


def already_recorded() -> str:
    return "Already recorded ✓"


def no_longer_listed() -> str:
    return "That product is no longer listed."


def callback_confirm_ack(confirmed: bool) -> str:
    return (
        "✅ Thank you — marked in stock."
        if confirmed
        else "❌ Got it — marked out of stock."
    )


def telegram_callback_response(product_name: str, confirmed: bool) -> str:
    return (
        f"✅ Thank you! '{product_name}' is marked in stock."
        if confirmed
        else f"❌ Got it — '{product_name}' is marked out of stock."
    )


def text_reply_ack(outcome: str, product_name: str | None = None) -> str:
    """One short line per outcome. Every failure names a way forward."""
    if outcome == "confirmed":
        return f"✅ Thanks — '{product_name}' is marked in stock."
    if outcome == "sold_out":
        return f"❌ Got it — '{product_name}' is marked out of stock."
    if outcome == "ambiguous":
        return (
            "Several products are waiting on you, so I'm not sure which one you "
            "mean. Send /myproducts, or reply directly to the message about it."
        )
    if outcome == "inactive":
        return "That request is no longer active — send /myproducts for a fresh list."
    if outcome == "orphan":
        return "Nothing is waiting on you right now. Send /myproducts to check."
    return (
        "I didn't catch that. Reply \"yes\" if it's still available or "
        '"sold out" if it isn\'t — or send /myproducts.'
    )


def prompt(
    brand: str,
    name: str,
    price: Decimal | str | float,
    currency: str,
    shop_name: str,
    is_available: bool = True,
) -> str:
    """The merchant-facing prompt: what we're asking about, and how to answer."""
    opening = "Is this still available?" if is_available else "Is this back in stock?"

    if not isinstance(price, Decimal):
        price = Decimal(str(price))

    return (
        f"{opening}\n\n"
        f"{brand} {name}\n"
        f"{price:,.2f} {currency} · {shop_name}\n\n"
        "Tap a button below, or just reply YES or NO."
    )


def generic_error() -> str:
    return "Something went wrong, please try again."


def unlink_prompt(shops_count: int = 1) -> str:
    if shops_count > 1:
        return (
            f"Are you sure you want to unlink this chat from your {shops_count} shops? "
            "You will stop receiving stock confirmation prompts here."
        )
    return (
        "Are you sure you want to unlink this chat from your shop? "
        "You will stop receiving stock confirmation prompts here."
    )


def unlink_confirmed(shops_count: int = 1) -> str:
    if shops_count > 1:
        return (
            f"✅ This chat has been unlinked from your {shops_count} shops. "
            "Send a new link code anytime to reconnect."
        )
    return (
        "✅ This chat has been unlinked from your shop. "
        "Send a new link code anytime to reconnect."
    )


def unlink_cancelled() -> str:
    return "Unlink cancelled. Your shop remains connected."


def digest(
    shop_name: str,
    items: list[tuple[int, str, str]],
    more_count: int = 0,
    hidden_after_days: int | None = None,
) -> str:
    """The merchant-facing weekly confirmation digest."""
    if hidden_after_days is None:
        from django.conf import settings

        hidden_after_days = getattr(settings, "CONFIRMATION_HIDDEN_AFTER_DAYS", 14)

    lines = [f"Weekly stock check for {shop_name}:", ""]
    for idx, label, price_str in items:
        lines.append(f"{idx}. {label} — {price_str}")

    if more_count > 0:
        lines.append(f"…and {more_count} more")

    lines.append("")
    lines.append(
        f"Tap the button below if everything is still in stock. "
        f"Unanswered listings are hidden after {hidden_after_days} days."
    )
    return "\n".join(lines)


def digest_ack(count: int) -> str:
    if count == 1:
        return "✅ Thanks! 1 product is confirmed in stock."
    return f"✅ Thanks! All {count} products are confirmed in stock."


def digest_already_done() -> str:
    return "This digest has already been answered. 🎉"


def review_item(
    index: int,
    total: int,
    label: str,
    price_str: str,
    age_days: int,
) -> str:
    days_str = (
        f"last confirmed {age_days} day ago"
        if age_days == 1
        else f"last confirmed {age_days} days ago"
    )
    return (
        f"Checking {index} of {total}:\n\n"
        f"{label}\n"
        f"{price_str} · {days_str}\n\n"
        "Is this still available?"
    )


def review_done(confirmed: int, sold: int) -> str:
    lines = ["Review complete! 🎉", ""]
    if confirmed:
        lines.append(
            f"✅ {confirmed} product marked in stock."
            if confirmed == 1
            else f"✅ {confirmed} products marked in stock."
        )
    if sold:
        lines.append(
            f"❌ {sold} product marked out of stock."
            if sold == 1
            else f"❌ {sold} products marked out of stock."
        )
    if not confirmed and not sold:
        lines.append("No changes were recorded.")
    return "\n".join(lines)


def review_paused() -> str:
    return (
        "Review paused. Send /myproducts or tap Check one by one anytime to continue."
    )


def digest_sold_ack(labels: list[str], remaining_count: int) -> str:
    if not labels:
        items_text = "Selected items"
    elif len(labels) == 1:
        items_text = f"'{labels[0]}'"
    else:
        items_text = ", ".join(f"'{label}'" for label in labels)
    verb = "is" if len(labels) == 1 else "are"

    if remaining_count == 0:
        return (
            f"❌ Got it — {items_text} {verb} marked out of stock.\n"
            "Nothing left to confirm. 🎉"
        )
    rem_label = (
        "1 product is" if remaining_count == 1 else f"{remaining_count} products are"
    )
    return (
        f"❌ Got it — {items_text} {verb} marked out of stock.\n"
        f"{rem_label} still waiting on you."
    )


def digest_ambiguous(indices: list[int]) -> str:
    if not indices:
        return "Are these products available, or sold?"
    if len(indices) == 1:
        return f"Is {indices[0]} available, or sold?"
    if len(indices) == 2:
        return f"Are {indices[0]} and {indices[1]} available, or sold?"
    joined = ", ".join(str(i) for i in indices[:-1])
    return f"Are {joined} and {indices[-1]} available, or sold?"


def digest_unparsed() -> str:
    return (
        'I didn\'t catch that. Reply "all" if everything is available, '
        '"sold 2 5" to mark specific items sold, or tap a button below.'
    )


def reminder(shop_name: str, count: int = 0) -> str:
    """Friendly nudge pointing back to the open digest."""
    if count == 1:
        return (
            f"Friendly reminder for {shop_name}: 1 product is still waiting for "
            "your confirmation. Tap below or reply 'all' if everything is available."
        )
    if count > 1:
        return (
            f"Friendly reminder for {shop_name}: {count} products are still waiting for "
            "your confirmation. Tap below or reply 'all' if everything is available."
        )
    return (
        f"Friendly reminder for {shop_name}: you have products waiting for "
        "your confirmation. Tap below or reply 'all' if everything is available."
    )


digest_reminder = reminder


def format_last_checkin(dt: datetime | None, now: datetime | None = None) -> str:
    """Format last check-in date/time in EAT.

    Returns:
    - "never" if dt is None
    - "today %H:%M" if dt falls on the same calendar day in EAT as now
    - "{day} {month_abbr} %H:%M" (e.g. "3 Sep 08:14") if earlier
    """
    if dt is None:
        return "never"

    if dt.tzinfo is None:
        local_dt = dt.replace(tzinfo=zoneinfo.ZoneInfo("UTC")).astimezone(EAT)
    else:
        local_dt = dt.astimezone(EAT)

    if now is None:
        local_now = datetime.now(EAT)
    elif now.tzinfo is None:
        local_now = now.replace(tzinfo=zoneinfo.ZoneInfo("UTC")).astimezone(EAT)
    else:
        local_now = now.astimezone(EAT)

    if local_dt.date() == local_now.date():
        return f"today {local_dt.strftime('%H:%M')}"
    return f"{local_dt.day} {local_dt.strftime('%b %H:%M')}"


def status(
    shop_name: str,
    fresh: int,
    due: int,
    hidden: int,
    last_checkin: datetime | str | None = None,
    prefix_shop: bool = False,
    *,
    last_checkin_str: str | None = None,
) -> str:
    """Format a shop status line.

    If 1 shop: one status line (prefix_shop=False).
    If several: one line per shop, prefixed with shop name (prefix_shop=True).
    """
    if last_checkin_str is not None:
        checkin_str = last_checkin_str
    elif isinstance(last_checkin, str):
        checkin_str = last_checkin
    else:
        checkin_str = format_last_checkin(last_checkin)

    body = (
        f"{fresh} fresh, {due} due soon, {hidden} hidden · "
        f"Last check-in: {checkin_str}"
    )
    if prefix_shop:
        return f"{shop_name}: {body}"
    return body


def sold_quick_ack(product_name: str) -> str:
    return f"❌ Got it — '{product_name}' is marked out of stock."


def sold_ambiguous(query: str) -> str:
    return f"Several live items match '{query}'. Tap one to mark it out of stock:"


def sold_not_found(query: str) -> str:
    return f"No live products matching '{query}' found. Send /myproducts to check your listings."


def sold_undone(product_name: str) -> str:
    return f"✅ Restored — '{product_name}' is back in stock on the storefront."


def shop_paused(count: int, shop_names: list[str]) -> str:
    names = ", ".join(shop_names) if shop_names else "your shop"
    if count == 0:
        return f"No active listings to pause for {names}."
    noun = "listing" if count == 1 else "listings"
    return (
        f"⏸ Vacation mode active: {count} {noun} paused for {names}.\n"
        "Your confirmation clocks are frozen. Send /resume_shop when you're back."
    )


def shop_resumed(count: int, shop_names: list[str]) -> str:
    names = ", ".join(shop_names) if shop_names else "your shop"
    if count == 0:
        return f"No paused listings to resume for {names}."
    noun = "listing" if count == 1 else "listings"
    return (
        f"▶️ Welcome back! {count} {noun} back on the storefront for {names}.\n"
        "Send /myproducts to review your inventory."
    )


def vacation_menu() -> str:
    return (
        "Vacation mode:\n"
        "Temporarily pause all listings when you take a break or close shop.\n"
        "Your confirmation freshness clock is preserved until you return.\n\n"
        "Choose an action below, or use /pause_shop and /resume_shop anytime."
    )
