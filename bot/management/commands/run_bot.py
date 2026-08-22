"""Single inbound Telegram process.

Telegram permits only one `getUpdates` consumer per bot token, so onboarding
(`/start`, `/help`) and stock-confirmation callbacks have to live in the same
application. Outbound sends stay in `bot.telegram_api`, which is synchronous --
`sweep_freshness` is a management command and PTB v22 is async-first.
"""

import logging
import math

from asgiref.sync import sync_to_async
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from bot.selectors import listing_for_chat
from bot.services import (
    handle_product_callback,
    handle_telegram_callback,
    handle_text_reply,
)

# Telegram truncates long button labels unhelpfully; do it ourselves so the
# product name stays readable.
BUTTON_LABEL_LIMIT = 55

logger = logging.getLogger(__name__)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    logger.info(
        "Inbound /start from user %s (chat %s)", update.effective_user.username, chat_id
    )
    # The chat ID is echoed back because it is exactly what a merchant needs to
    # paste into their shop record before confirmation prompts can reach them.
    text = (
        "Welcome to tinsae marketplace\n"
        f"Your chat ID is {chat_id}\n"
        "enter /help for help."
    )
    await update.message.reply_text(text=text)
    logger.info("Outbound reply to chat %s: %r", chat_id, text)


async def help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    logger.info(
        "Inbound /help from user %s (chat %s)", update.effective_user.username, chat_id
    )
    text = (
        "This is help page what do you want to know?\n"
        "/myproducts — list the products waiting on your confirmation."
    )
    await update.message.reply_text(text=text)
    logger.info("Outbound reply to chat %s: %r", chat_id, text)


def _render_listing(shops, products, page: int):
    """Build one page of the confirmation listing.

    `page` is clamped rather than rejected: the list shrinks as the merchant
    answers, so a stale page number from an older message is normal, not an error.
    """
    page_size = settings.MYPRODUCTS_PAGE_SIZE
    total = len(products)
    pages = max(1, math.ceil(total / page_size))
    page = max(0, min(page, pages - 1))
    start = page * page_size
    visible = products[start : start + page_size]
    label_shop = len(shops) > 1

    rows = []
    for product in visible:
        label = f"{product.brand} {product.name}"
        if label_shop:
            label = f"{product.shop.name} — {label}"
        rows.append(
            [
                InlineKeyboardButton(
                    f"✅ {label}"[:BUTTON_LABEL_LIMIT],
                    callback_data=f"pconfirm:{product.pk}:{page}",
                ),
                InlineKeyboardButton("❌", callback_data=f"pdeny:{product.pk}:{page}"),
            ]
        )

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("◀ Prev", callback_data=f"page:{page - 1}"))
    if page < pages - 1:
        nav.append(InlineKeyboardButton("Next ▶", callback_data=f"page:{page + 1}"))
    if nav:
        rows.append(nav)

    text = (
        f"{total} product{'' if total == 1 else 's'} waiting on you "
        f"(page {page + 1} of {pages}).\n"
        "Tap ✅ if it is still available, ❌ if it is out of stock."
    )
    return text, InlineKeyboardMarkup(rows)


async def my_products(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """List the products this chat's shops need to confirm."""
    chat_id = update.effective_chat.id
    logger.info("Inbound /myproducts from chat %s", chat_id)

    shops, products = await sync_to_async(listing_for_chat, thread_sensitive=True)(
        chat_id
    )

    if not shops:
        await update.message.reply_text(
            "This chat is not linked to a shop yet. Send /start, then paste the "
            "chat ID it gives you into your shop record."
        )
        return

    if not products:
        await update.message.reply_text("Nothing needs confirming right now. 🎉")
        return

    text, markup = _render_listing(shops, products, page=0)
    await update.message.reply_text(text, reply_markup=markup)


async def _refresh_listing(query, chat_id, page: int):
    """Redraw the listing in place, so the chat does not fill with copies."""
    shops, products = await sync_to_async(listing_for_chat, thread_sensitive=True)(
        chat_id
    )
    if not products:
        await query.edit_message_text("Nothing left to confirm. 🎉")
        return

    text, markup = _render_listing(shops, products, page=page)
    await query.edit_message_text(text, reply_markup=markup)


async def _on_listing_answer(query, chat_id, data: str):
    product = await sync_to_async(handle_product_callback, thread_sensitive=True)(
        callback_data=data,
        chat_id=str(chat_id),
    )
    if product is None:
        # Could be a foreign chat, a deleted product, or ✅ on something already
        # out of stock. The merchant does not need to know which; they need a way
        # forward that does not involve an out-of-date message.
        await query.answer("Couldn't record that — send /myproducts for a fresh list.")
        return

    await query.answer("Recorded")
    # The answered row leaves the list, so redraw to keep the count honest.
    await _refresh_listing(query, chat_id, page=_page_from(data, index=2))


def _page_from(data: str, index: int) -> int:
    parts = data.split(":")
    if len(parts) > index and parts[index].isdigit():
        return int(parts[index])
    return 0


async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Route every inline-button press: listing paging, listing answers, prompts."""
    query = update.callback_query
    if query is None:
        return

    chat_id = query.message.chat.id if query.message else None
    data = query.data or ""
    logger.info("Inbound callback %r from chat %s", data, chat_id)

    # /myproducts buttons are colon-separated and product-keyed; the original
    # inline prompts are underscore-separated and request-keyed. Route on that.
    if data.startswith("page:"):
        await query.answer()
        await _refresh_listing(query, chat_id, page=_page_from(data, index=1))
        return
    if data.startswith(("pconfirm:", "pdeny:")):
        await _on_listing_answer(query, chat_id, data)
        return

    # The service owns authorisation and the state change. It is handed only the
    # data and the chat: replying to Telegram is PTB's job here, so we withhold
    # callback_query_id/message_id rather than trigger the sync transport too.
    handled = await sync_to_async(handle_telegram_callback, thread_sensitive=True)(
        callback_data=data,
        chat_id=str(chat_id),
    )

    if not handled:
        await query.answer("This request is no longer active.")
        return

    action = data.partition("_")[0]
    await query.answer("Response recorded")
    if query.message:
        await query.edit_message_text(
            "✅ Thank you — marked in stock."
            if action == "confirm"
            else "❌ Got it — marked out of stock."
        )


def _text_reply_ack(outcome: str, product) -> str:
    """One short line per outcome. Every failure names a way forward."""
    if outcome == "confirmed":
        return f"✅ Thanks — '{product.name}' is marked in stock."
    if outcome == "sold_out":
        return f"❌ Got it — '{product.name}' is marked out of stock."
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


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Apply a free-text answer, or explain why it could not be applied."""
    message = update.message
    if message is None or not message.text:
        return

    chat_id = update.effective_chat.id
    reply_to = message.reply_to_message.message_id if message.reply_to_message else None
    logger.info(
        "Inbound text %r from chat %s (reply_to=%s)", message.text, chat_id, reply_to
    )

    outcome, product = await sync_to_async(handle_text_reply, thread_sensitive=True)(
        text=message.text,
        chat_id=str(chat_id),
        reply_to_message_id=reply_to,
    )
    await message.reply_text(_text_reply_ack(outcome, product))


class Command(BaseCommand):
    help = "Runs the Telegram listener: onboarding, /myproducts, and stock callbacks."

    def handle(self, *args, **options):
        token = settings.TELEGRAM_BOT_TOKEN
        if not token:
            raise CommandError(
                "TELEGRAM_BOT_TOKEN is not set. Add it to your .env file."
            )

        app = Application.builder().token(token).build()
        app.add_handler(CommandHandler("start", start))
        app.add_handler(CommandHandler("help", help))
        app.add_handler(CommandHandler("myproducts", my_products))
        app.add_handler(CallbackQueryHandler(on_callback))
        # Last, and command-free: a merchant who ignores the buttons and just
        # types "sold out" still gets an answer recorded.
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))

        logger.info("Starting Telegram long-polling...")
        app.run_polling()
