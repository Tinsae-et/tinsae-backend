"""Register the bot's command list with Telegram.

Telegram shows this list behind the client's ☰ / "/" menu. `run_bot` calls the
same function at startup; this command exists so it can be run (and tested)
without starting the poller.
"""

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from bot import telegram_api as telegram
from bot.messages import BOT_COMMANDS


class Command(BaseCommand):
    help = "Register the bot's commands with Telegram (the client menu)."

    def handle(self, *args, **options):
        if not settings.TELEGRAM_BOT_TOKEN:
            raise CommandError(
                "TELEGRAM_BOT_TOKEN is not set. Add it to your .env file."
            )

        telegram.set_my_commands(BOT_COMMANDS)
        self.stdout.write(
            self.style.SUCCESS(
                f"Registered {len(BOT_COMMANDS)} commands with Telegram."
            )
        )
