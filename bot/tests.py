from django.core.management import CommandError, call_command
from django.test import SimpleTestCase, override_settings


class RunBotCommandTest(SimpleTestCase):
    def test_run_bot_requires_token(self):
        with override_settings(TELEGRAM_BOT_TOKEN=""):
            with self.assertRaisesMessage(
                CommandError, "TELEGRAM_BOT_TOKEN is not set"
            ):
                call_command("run_bot")
