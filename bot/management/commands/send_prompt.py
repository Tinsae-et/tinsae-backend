"""Send one confirmation prompt, by hand.

`sweep_freshness` decides *which* products to ask about; this command asks about
the one you name. It prints the composed message whether or not a token is set --
checking the wording should not require sending anything to a real merchant.
The prompt is enqueued to the outbox; delivery is handled by the Celery worker.

The gate checks below duplicate `can_prompt`'s, deliberately: the service returns
a bare None for every refusal, and someone running this by hand needs to be told
which refusal it was.
"""

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from bot import telegram_api as telegram
from bot.models import ConfirmationRequest
from bot.services import prompt_text, send_confirmation_prompt
from catalog.models import Product


class Command(BaseCommand):
    help = "Send a stock-confirmation prompt for one product."

    def add_arguments(self, parser):
        parser.add_argument("product", help="Product primary key or slug.")
        parser.add_argument(
            "--force",
            action="store_true",
            help=(
                "Send even while ENABLE_TELEGRAM_BOT_PROMPTS is False. Lifts the "
                "policy flag only -- an unlinked shop or an outstanding request "
                "still blocks the send."
            ),
        )

    def handle(self, *args, **options):
        product = self._get_product(options["product"])
        shop = product.shop

        self.stdout.write(f"Product: {product} (#{product.pk})")
        self.stdout.write("Message:")
        self.stdout.write(prompt_text(product))
        self.stdout.write("")

        if not shop.telegram_chat_id:
            raise CommandError(
                f"Shop '{shop.name}' has no telegram_chat_id, so there is nowhere "
                "to send this. Ask the merchant to send /start to the bot and "
                "paste the chat ID it returns into the shop record."
            )

        if ConfirmationRequest.objects.filter(
            product=product, status="pending"
        ).exists():
            self.stdout.write(
                self.style.WARNING(
                    "Skipped: this product already has a pending request, so it "
                    "will not be asked twice. Nothing was sent."
                )
            )
            return

        prompts_enabled = getattr(settings, "ENABLE_TELEGRAM_BOT_PROMPTS", False)
        if not prompts_enabled and not options["force"]:
            self.stdout.write(
                self.style.WARNING(
                    "Skipped: ENABLE_TELEGRAM_BOT_PROMPTS is False. Re-run with "
                    "--force to send this one anyway."
                )
            )
            return

        request = send_confirmation_prompt(product, force=options["force"])
        if request is None:
            raise CommandError("The prompt was not sent. Check the log for the reason.")

        if telegram.is_configured():
            self.stdout.write(
                self.style.SUCCESS(
                    f"Queued for chat {shop.telegram_chat_id} "
                    f"(ConfirmationRequest #{request.pk}, pending). "
                    "The Celery worker will deliver it."
                )
            )
        else:
            self.stdout.write(
                self.style.WARNING(
                    "TELEGRAM_BOT_TOKEN is not set, so the queued message will be "
                    "marked dead by the sender until a token is configured. "
                    f"ConfirmationRequest #{request.pk} was created."
                )
            )

    def _get_product(self, identifier: str) -> Product:
        lookup = (
            {"pk": int(identifier)} if identifier.isdigit() else {"slug": identifier}
        )
        try:
            return Product.objects.select_related("shop").get(**lookup)
        except Product.DoesNotExist:
            raise CommandError(f"No product matching {identifier!r}.") from None
