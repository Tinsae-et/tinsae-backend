"""User-facing message strings for the Telegram bot.

Every merchant-facing string is defined here as a pure function returning plain
text. No Django model imports, no ORM, no Telegram dependencies, and no HTML
escaping (plain text v1; formatting is deferred per D21).
"""

from decimal import Decimal


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
        "This is the help page — what do you want to know?\n"
        "/link <code> — connect this chat to your shop.\n"
        "/myproducts — list the products waiting on your confirmation."
    )


def link_usage() -> str:
    return "Send the code along with the command, like this:\n/link TIN-ABC2345"


def link_ack(outcome: str, shop_name: str | None = None) -> str:
    """One short line per link outcome. Every failure names a way forward."""
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
    return (
        f"{total} product{'' if total == 1 else 's'} waiting on you "
        f"(page {page + 1} of {pages}).\n"
        "Tap ✅ if it is still available, ❌ if it is out of stock."
    )


def not_linked() -> str:
    return (
        "This chat is not linked to a shop yet. Send your shop's link code — "
        "like /link TIN-ABC2345 — or ask us for one."
    )


def nothing_needs_confirming() -> str:
    return "Nothing needs confirming right now. 🎉"


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
