from django.conf import settings
from django.core.management.base import BaseCommand
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        text="Welcome to tinsae marketplace\n enter /help for help.",
    )


async def help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(text="This is help page what do you want to know?")


class Command(BaseCommand):
    def handle(self, *args, **options):
        token = settings.BOT_ACCESS_TOKEN

        app = Application.builder().token(token).build()
        app.add_handler(CommandHandler("start", start))
        app.add_handler(CommandHandler("help", help))

        app.run_polling()
