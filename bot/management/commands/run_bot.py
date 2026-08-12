import time

from django.core.management.base import BaseCommand

from bot import telegram
from bot.services import handle_telegram_callback


class Command(BaseCommand):
    help = "Polls Telegram for merchant callback responses (local dev; no queue)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--once",
            action="store_true",
            help="Single poll instead of a continuous loop.",
        )
        parser.add_argument(
            "--timeout",
            type=int,
            default=30,
            help="Long-poll timeout in seconds (default: 30).",
        )

    def handle(self, *args, **options):
        if not telegram.is_configured():
            self.stdout.write(
                self.style.WARNING("TELEGRAM_BOT_TOKEN is not configured. Exiting.")
            )
            return

        run_once = options["once"]
        timeout = options["timeout"]
        offset = None

        self.stdout.write(self.style.SUCCESS("Starting Telegram bot listener..."))

        while True:
            try:
                updates = telegram.get_updates(offset=offset, timeout=timeout)
            except telegram.TelegramError as exc:
                self.stdout.write(self.style.ERROR(f"Polling failed: {exc}"))
                if run_once:
                    return
                time.sleep(5)
                continue

            for update in updates:
                offset = update["update_id"] + 1
                callback = update.get("callback_query")
                if not callback:
                    continue

                message = callback.get("message") or {}
                self.stdout.write(f"Received callback: {callback.get('data', '')}")
                handle_telegram_callback(
                    callback_data=callback.get("data", ""),
                    chat_id=str(message.get("chat", {}).get("id", "")),
                    callback_query_id=callback.get("id"),
                    message_id=message.get("message_id"),
                )

            if run_once:
                return
