"""Event kinds, channel routing, deduplication keys, and renderers for notifications."""

from collections.abc import Callable
from dataclasses import dataclass

IN_APP = "in_app"
TELEGRAM = "telegram"
EMAIL = "email"

MAX_IN_APP_BODY_LENGTH = 140


def _truncate_body(body: str, max_length: int = MAX_IN_APP_BODY_LENGTH) -> str:
    body = body.strip() if body else ""
    if len(body) > max_length:
        return body[:max_length] + "…"
    return body


@dataclass(frozen=True)
class InApp:
    type: str  # frontend NotificationType
    title: str
    body: str
    cta_label: str


def _inquiry_message_in_app(payload: dict) -> InApp:
    product = payload.get("product_name")
    title = f"New message about {product}" if product else "New message from a buyer"
    raw_body = payload.get("body") or payload.get("text") or ""
    return InApp(
        type="message",
        title=title,
        body=_truncate_body(raw_body),
        cta_label="Reply",
    )


def _inquiry_reply_in_app(payload: dict) -> InApp:
    shop = payload.get("shop_name")
    title = f"{shop} replied" if shop else "The shop replied"
    raw_body = payload.get("body") or payload.get("text") or ""
    return InApp(
        type="reply",
        title=title,
        body=_truncate_body(raw_body),
        cta_label="Open chat",
    )


def _inquiry_unanswered_in_app(payload: dict) -> InApp:
    product = payload.get("product_name")
    body = f"About {product}" if product else ""
    return InApp(
        type="message",
        title="A buyer is waiting for your reply",
        body=body,
        cta_label="Reply",
    )


def _verification_reviewed_in_app(payload: dict) -> InApp:
    state = payload.get("state")
    if state == "approved":
        title = "Your shop is verified"
        body = "Buyers now see your verified badge."
    else:
        title = "Verification needs changes"
        body = "Review the feedback and resubmit."
    return InApp(
        type="verify",
        title=title,
        body=body,
        cta_label="View",
    )


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
        return (
            f"Reminder: You have an unanswered inquiry regarding {product}.\n\n"
            f"View inquiry: {url}"
        )
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
    in_app: Callable[[dict], InApp] | None = None

    def __post_init__(self):
        if TELEGRAM in self.channels and self.telegram_text is None:
            raise ValueError(
                f"Kind {self.name!r} includes TELEGRAM channel but telegram_text is None."
            )
        if IN_APP in self.channels and self.in_app is None:
            raise ValueError(
                f"Kind {self.name!r} includes IN_APP channel but in_app is None."
            )


# Link paths: inquiry links are TBD until the conversation UI exists;
# verification link points to the frontend verification flow.
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
        in_app=_inquiry_message_in_app,
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
        in_app=_inquiry_reply_in_app,
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
        in_app=_inquiry_unanswered_in_app,
    ),
    "verification_reviewed": Kind(
        name="verification_reviewed",
        channels=(IN_APP, TELEGRAM),
        dedupe_key=lambda p: f"verification:{p['request_id']}:{p['state']}",
        link=lambda p: "/shop/dashboard/get-verified",
        telegram_text=_verification_reviewed_telegram_text,
        telegram_silent_at_night=False,
        email_batched=False,
        in_app=_verification_reviewed_in_app,
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
