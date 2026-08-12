"""Single inbound Telegram process.

Telegram permits only one `getUpdates` consumer per bot token, so onboarding
(`/start`, `/help`) and stock-confirmation callbacks have to live in the same
application. Outbound sends stay in `bot.telegram_api`, which is synchronous --
`sweep_freshness` is a management command and PTB v22 is async-first.
"""

import logging

from asgiref.sync import sync_to_async
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from telegram import Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

from bot.services import handle_telegram_callback

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
    text = "This is help page what do you want to know?"
    await update.message.reply_text(text=text)
    logger.info("Outbound reply to chat %s: %r", chat_id, text)


async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Apply a merchant's inline-button answer to a confirmation prompt."""
    query = update.callback_query
    if query is None:
        return

    chat_id = query.message.chat.id if query.message else None
    logger.info("Inbound callback %r from chat %s", query.data, chat_id)

    # The service owns authorisation and the state change. It is handed only the
    # data and the chat: replying to Telegram is PTB's job here, so we withhold
    # callback_query_id/message_id rather than trigger the sync transport too.
    handled = await sync_to_async(handle_telegram_callback, thread_sensitive=True)(
        callback_data=query.data or "",
        chat_id=str(chat_id),
    )

    if not handled:
        await query.answer("This request is no longer active.")
        return

    action = (query.data or "").partition("_")[0]
    await query.answer("Response recorded")
    if query.message:
        await query.edit_message_text(
            "✅ Thank you — marked in stock."
            if action == "confirm"
            else "❌ Got it — marked out of stock."
        )


class Command(BaseCommand):
    help = "Runs the Telegram listener: onboarding commands and stock callbacks."

    def handle(self, *args, **options):
        token = settings.TELEGRAM_BOT_TOKEN
        if not token:
            raise CommandError(
                "TELEGRAM_BOT_TOKEN is not set. Add it to your .env file."
            )

        app = Application.builder().token(token).build()
        app.add_handler(CommandHandler("start", start))
        app.add_handler(CommandHandler("help", help))
        app.add_handler(CallbackQueryHandler(on_callback))

        logger.info("Starting Telegram long-polling...")
        app.run_polling()
