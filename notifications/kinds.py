"""Event kinds, channel routing, deduplication keys, and renderers for notifications."""

from collections.abc import Callable
from dataclasses import dataclass

IN_APP = "in_app"
TELEGRAM = "telegram"
EMAIL = "email"


def _inquiry_message_telegram_text(payload: dict, url: str) -> str:
    # TBD: Amharic via Shop.language is a follow-up. English only for now.
    product = payload.get("product_name")
    body = payload.get("body") or payload.get("text") or ""
    if product:
        header = f"New inquiry regarding {product}:"
    else:
        header = "New inquiry received:"
    if body:
        return f'{header}\n\n"{body}"\n\nView inquiry: {url}'
    return f"{header}\n\nView inquiry: {url}"


def _inquiry_unanswered_telegram_text(payload: dict, url: str) -> str:
    # TBD: Amharic via Shop.language is a follow-up. English only for now.
    product = payload.get("product_name")
    if product:
        return f"Reminder: You have an unanswered inquiry regarding {product}.\n\nView inquiry: {url}"
    return f"Reminder: You have an unanswered inquiry.\n\nView inquiry: {url}"


def _verification_reviewed_telegram_text(payload: dict, url: str) -> str:
    # TBD: Amharic via Shop.language is a follow-up. English only for now.
    state = payload.get("state", "reviewed")
    return f"Your shop verification request has been {state}.\n\nView details: {url}"


@dataclass(frozen=True)
class Kind:
    name: str
    channels: tuple[str, ...]
    dedupe_key: Callable[[dict], str]
    link: Callable[[dict], str]
    telegram_text: Callable[[dict, str], str] | None = None
    telegram_silent_at_night: bool = False  # True -> sent during quiet hours, silently
    email_batched: bool = False

    def __post_init__(self):
        if TELEGRAM in self.channels and self.telegram_text is None:
            raise ValueError(
                f"Kind {self.name!r} includes TELEGRAM channel but telegram_text is None."
            )


# All link paths are TBD: confirm path with frontend
KINDS: dict[str, Kind] = {
    "inquiry_message": Kind(
        name="inquiry_message",
        channels=(IN_APP, TELEGRAM),
        dedupe_key=lambda p: f"inquiry_message:{p['message_id']}",
        # TBD: confirm path with frontend
        link=lambda p: f"/dashboard/inquiries/{p['inquiry_id']}",
        telegram_text=_inquiry_message_telegram_text,
        telegram_silent_at_night=True,
        email_batched=False,
    ),
    "inquiry_reply": Kind(
        name="inquiry_reply",
        channels=(IN_APP, EMAIL),
        dedupe_key=lambda p: f"inquiry_reply:{p['message_id']}",
        # TBD: confirm path with frontend
        link=lambda p: f"/inquiries/{p['inquiry_id']}",
        telegram_text=None,
        telegram_silent_at_night=False,
        email_batched=True,
    ),
    "inquiry_unanswered": Kind(
        name="inquiry_unanswered",
        channels=(IN_APP, TELEGRAM),
        dedupe_key=lambda p: f"inquiry_unanswered:{p['inquiry_id']}",
        # TBD: confirm path with frontend
        link=lambda p: f"/dashboard/inquiries/{p['inquiry_id']}",
        telegram_text=_inquiry_unanswered_telegram_text,
        telegram_silent_at_night=False,
        email_batched=False,
    ),
    "verification_reviewed": Kind(
        name="verification_reviewed",
        channels=(IN_APP, TELEGRAM),
        dedupe_key=lambda p: f"verification:{p['request_id']}:{p['state']}",
        # TBD: confirm path with frontend
        link=lambda p: f"/dashboard/shop/{p['shop_slug']}/verification",
        telegram_text=_verification_reviewed_telegram_text,
        telegram_silent_at_night=False,
        email_batched=False,
    ),
}


def get_kind(name: str) -> Kind:
    """Look up a notification Kind by name.

    Raises ValueError if kind name is unknown (programming error, fail loud).
    """
    try:
        return KINDS[name]
    except KeyError:
        raise ValueError(f"Unknown notification kind: {name!r}") from None
