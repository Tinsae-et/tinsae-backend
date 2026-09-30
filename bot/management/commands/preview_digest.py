"""Read-only preview of confirmation digests that would be sent to shops.

Tooling command that does NOT write to the database. Reuses build_digest and
due_products_for_shop to faithfully show the message text, markup, dedupe key,
and send/skip decision.
"""

from datetime import datetime, time, timedelta

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from bot.digest import build_digest
from bot.selectors import due_products_for_shop
from shops.models import Shop


class Command(BaseCommand):
    help = (
        "Read-only preview of the weekly confirmation digest for a shop or all shops. "
        "Does not write to the database or send any messages."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "shop",
            nargs="?",
            default=None,
            help="Shop primary key or slug to preview.",
        )
        parser.add_argument(
            "--all",
            action="store_true",
            dest="all_shops",
            help="Preview for all candidate shops (linked and not blocked).",
        )
        parser.add_argument(
            "--date",
            type=str,
            default=None,
            help="Target EAT date (YYYY-MM-DD), default: today.",
        )

    def handle(self, *args, **options):
        shop_arg = options.get("shop")
        all_shops = options.get("all_shops", False)
        date_arg = options.get("date")

        if not shop_arg and not all_shops:
            raise CommandError("Please specify a shop (pk or slug) or use --all.")
        if shop_arg and all_shops:
            raise CommandError("Cannot specify both a shop and --all.")

        now = timezone.now()
        eat_now = timezone.localtime(now)
        default_date = eat_now.date()

        if date_arg:
            try:
                target_date = datetime.strptime(date_arg, "%Y-%m-%d").date()
            except ValueError as exc:
                raise CommandError(
                    f"Invalid date format {date_arg!r}; expected YYYY-MM-DD."
                ) from exc
            target_dt = timezone.make_aware(
                datetime.combine(target_date, time(8, 0)),
                timezone=eat_now.tzinfo,
            )
        else:
            target_date = default_date
            target_dt = now

        cutoff = target_dt - timedelta(days=settings.CONFIRMATION_INTERVAL_DAYS)

        if all_shops:
            self._handle_all(target_date, cutoff)
        else:
            shop = self._get_shop(shop_arg)
            info = self._evaluate_shop(shop, target_date, cutoff)
            self.stdout.write(self._format_full_block(info, target_date))

    def _get_shop(self, shop_arg: str) -> Shop:
        if shop_arg.isdigit():
            shop = Shop.objects.filter(pk=int(shop_arg)).first()
            if shop:
                return shop
        shop = Shop.objects.filter(slug=shop_arg).first()
        if shop:
            return shop
        raise CommandError(f"Shop '{shop_arg}' not found.")

    def _evaluate_shop(self, shop: Shop, target_date, cutoff) -> dict:
        interval_days = settings.CONFIRMATION_INTERVAL_DAYS

        has_chat_id = bool(shop.telegram_chat_id)
        is_blocked = shop.blocked_at is not None

        last_digest = shop.confirmation_digests.order_by("-for_date").first()
        if last_digest:
            days_since_last = (target_date - last_digest.for_date).days
            if days_since_last < interval_days:
                cadence_status = (
                    f"within interval ({days_since_last}d < {interval_days}d, "
                    f"last on {last_digest.for_date})"
                )
                cadence_due = False
            else:
                cadence_status = (
                    f"due ({days_since_last}d >= {interval_days}d, "
                    f"last on {last_digest.for_date})"
                )
                cadence_due = True
        else:
            days_since_last = None
            cadence_status = "due (no previous digest)"
            cadence_due = True

        existing_digest = shop.confirmation_digests.filter(for_date=target_date).first()
        has_existing = existing_digest is not None

        due_products = due_products_for_shop(shop, cutoff)
        due_count = len(due_products)

        _, text, markup = build_digest(
            shop,
            digest_id=0,
            cutoff=cutoff,
            due_products=due_products,
        )
        button_labels = [
            btn.text for row in getattr(markup, "inline_keyboard", []) for btn in row
        ]

        dedupe_key = f"digest:{shop.pk}:{target_date}"

        if not has_chat_id:
            verdict = "WOULD SKIP (no telegram_chat_id)"
            would_send = False
        elif is_blocked:
            verdict = "WOULD SKIP (bot blocked by user)"
            would_send = False
        elif has_existing:
            verdict = f"WOULD SKIP (digest already exists for {target_date})"
            would_send = False
        elif not cadence_due:
            verdict = (
                f"WOULD SKIP (cadence: last digest was on {last_digest.for_date}, "
                f"{days_since_last}d < {interval_days}d)"
            )
            would_send = False
        elif due_count == 0:
            verdict = "WOULD SKIP (no products due)"
            would_send = False
        else:
            verdict = "WOULD SEND"
            would_send = True

        return {
            "shop": shop,
            "has_chat_id": has_chat_id,
            "is_blocked": is_blocked,
            "last_digest": last_digest,
            "cadence_status": cadence_status,
            "cadence_due": cadence_due,
            "has_existing": has_existing,
            "due_count": due_count,
            "due_products": due_products,
            "text": text,
            "button_labels": button_labels,
            "dedupe_key": dedupe_key,
            "verdict": verdict,
            "would_send": would_send,
        }

    def _format_full_block(self, info: dict, target_date) -> str:
        shop = info["shop"]
        chat_str = f"yes ({shop.telegram_chat_id})" if info["has_chat_id"] else "no"
        blocked_str = f"yes ({shop.blocked_at})" if info["is_blocked"] else "no"
        existing_str = "yes (would be a no-op)" if info["has_existing"] else "no"
        buttons_str = (
            ", ".join(info["button_labels"]) if info["button_labels"] else "none"
        )

        lines = [
            f"Shop: {shop.name} ({shop.pk})",
            f"Telegram chat: {chat_str}",
            f"Blocked: {blocked_str}",
            f"Cadence gate: {info['cadence_status']}",
            f"Existing digest for {target_date}: {existing_str}",
            f"Due products: {info['due_count']}",
            "Rendered message:",
            "----------------------------------------",
            info["text"],
            "----------------------------------------",
            f"Buttons: {buttons_str}",
            f"Dedupe key: {info['dedupe_key']}",
            f"Verdict: {info['verdict']}",
        ]
        return "\n".join(lines)

    def _handle_all(self, target_date, cutoff):
        candidates = list(
            Shop.objects.exclude(telegram_chat_id="")
            .filter(blocked_at__isnull=True)
            .order_by("pk")
        )

        if not candidates:
            self.stdout.write("No candidate shops found (linked and not blocked).")
            return

        self.stdout.write(
            f"Evaluating {len(candidates)} candidate shop(s) for {target_date}:\n"
        )
        would_send_list = []
        for shop in candidates:
            info = self._evaluate_shop(shop, target_date, cutoff)
            self.stdout.write(f"- {shop.name} ({shop.pk}): {info['verdict']}")
            if info["would_send"]:
                would_send_list.append(info)

        if would_send_list:
            self.stdout.write(
                f"\nFull digest preview for {len(would_send_list)} shop(s) that WOULD SEND:\n"
            )
            for info in would_send_list:
                self.stdout.write("=" * 60)
                self.stdout.write(self._format_full_block(info, target_date))
                self.stdout.write("=" * 60 + "\n")
        else:
            self.stdout.write(f"\n0 shops would receive a digest for {target_date}.")
