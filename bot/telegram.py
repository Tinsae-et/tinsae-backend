"""Transport layer for the Telegram Bot API.

All outbound HTTP lives here so tests have a single seam to patch. Uses urllib
from the standard library — no new dependency (see .agents/AGENTS.md §2).

Note this module is only about *reaching* Telegram. Whether we should prompt at
all is policy and lives behind ENABLE_TELEGRAM_BOT_PROMPTS in services.py.
"""

import json
import logging
import urllib.error
import urllib.request

from django.conf import settings

logger = logging.getLogger(__name__)

API_ROOT = "https://api.telegram.org"
DEFAULT_TIMEOUT = 5


class TelegramError(Exception):
    """The API was unreachable, timed out, or returned ok=false."""


def is_configured() -> bool:
    return bool(getattr(settings, "TELEGRAM_BOT_TOKEN", None))


def _call(method: str, payload: dict, timeout: int = DEFAULT_TIMEOUT):
    token = getattr(settings, "TELEGRAM_BOT_TOKEN", None)
    if not token:
        raise TelegramError("TELEGRAM_BOT_TOKEN is not configured.")

    request = urllib.request.Request(
        f"{API_ROOT}/bot{token}/{method}",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        raise TelegramError(f"{method} failed: {exc}") from exc

    if not body.get("ok"):
        raise TelegramError(f"{method} returned ok=false: {body.get('description')}")
    return body.get("result", {})


def send_message(chat_id, text: str, reply_markup: dict | None = None) -> dict:
    payload = {"chat_id": str(chat_id), "text": text}
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup
    return _call("sendMessage", payload)


def answer_callback(callback_query_id, text: str = "") -> dict:
    return _call(
        "answerCallbackQuery",
        {"callback_query_id": callback_query_id, "text": text},
    )


def edit_message(chat_id, message_id, text: str) -> dict:
    return _call(
        "editMessageText",
        {"chat_id": str(chat_id), "message_id": message_id, "text": text},
    )


def get_updates(offset=None, timeout: int = 30) -> list:
    payload = {"timeout": timeout}
    if offset is not None:
        payload["offset"] = offset
    result = _call("getUpdates", payload, timeout=timeout + 5)
    return result if isinstance(result, list) else []
