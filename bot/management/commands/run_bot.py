import logging

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

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
    text = "Welcome to tinsae marketplace\n enter /help for help."
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


class Command(BaseCommand):
    def handle(self, *args, **options):
        token = settings.TELEGRAM_BOT_TOKEN
        if not token:
            raise CommandError(
                "TELEGRAM_BOT_TOKEN is not set. Add it to your .env file."
            )

        app = Application.builder().token(token).build()
        app.add_handler(CommandHandler("start", start))
        app.add_handler(CommandHandler("help", help))

        logger.info("Starting Telegram long-polling...")
        app.run_polling()
