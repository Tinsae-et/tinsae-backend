"""Minimal sync transport for the Telegram Bot API.

All outbound HTTP lives here so tests have a single seam to patch. Uses urllib
from the standard library rather than python-telegram-bot because the callers
are synchronous -- `sweep_freshness` is a management command, and PTB v22 is
async-first. PTB owns the *inbound* side in `run_bot`.

Named `telegram_api` rather than `telegram` so it cannot be confused with the
installed `telegram` package that PTB provides.

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
    """Base error: the API was unreachable, timed out, or returned ok=false."""


class TelegramRetryAfter(TelegramError):
    """429 Too Many Requests; honour the retry_after seconds exactly."""

    def __init__(self, retry_after: int, description: str = ""):
        self.retry_after = retry_after
        self.description = description
        super().__init__(
            f"Too Many Requests: retry after {retry_after}s ({description})"
        )


class TelegramForbidden(TelegramError):
    """403 Forbidden; bot was blocked by the user or lacks permission."""

    def __init__(self, description: str = ""):
        self.description = description
        super().__init__(f"Forbidden: {description}")


class TelegramBadRequest(TelegramError):
    """400 Bad Request; our bug — do not retry."""

    def __init__(self, description: str = ""):
        self.description = description
        super().__init__(f"Bad Request: {description}")


class TelegramNetworkError(TelegramError):
    """Network connection failure or timeout."""


def is_configured() -> bool:
    return bool(getattr(settings, "TELEGRAM_BOT_TOKEN", None))


def _parse_telegram_error(
    error_code: int | None, description: str, parameters: dict | None = None
) -> TelegramError:
    parameters = parameters or {}
    if error_code == 429:
        retry_after = int(parameters.get("retry_after", 1))
        return TelegramRetryAfter(retry_after=retry_after, description=description)
    if error_code == 403:
        return TelegramForbidden(description=description)
    if error_code == 400:
        return TelegramBadRequest(description=description)
    return TelegramError(f"Telegram API error {error_code}: {description}")


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
    except urllib.error.HTTPError as exc:
        try:
            err_body = json.loads(exc.read().decode("utf-8"))
            error_code = err_body.get("error_code", exc.code)
            description = err_body.get("description", str(exc))
            parameters = err_body.get("parameters", {})
        except Exception:
            error_code = exc.code
            description = str(exc)
            parameters = {}
        raise _parse_telegram_error(error_code, description, parameters) from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise TelegramNetworkError(f"{method} network error: {exc}") from exc
    except ValueError as exc:
        raise TelegramError(f"{method} returned invalid JSON: {exc}") from exc

    if not body.get("ok"):
        error_code = body.get("error_code")
        description = body.get("description", "")
        parameters = body.get("parameters", {})
        raise _parse_telegram_error(error_code, description, parameters)
    return body.get("result", {})


def send_message(
    chat_id,
    text: str,
    reply_markup: dict | None = None,
    disable_notification: bool = False,
) -> dict:
    payload = {"chat_id": str(chat_id), "text": text}
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup
    if disable_notification:
        payload["disable_notification"] = True
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


def set_my_commands(commands: list[tuple[str, str]]) -> dict:
    """Register the bot's command list so the client ☰ menu shows it.

    `commands` is a list of (command, description) pairs, without the leading "/".
    """
    return _call(
        "setMyCommands",
        {
            "commands": [
                {"command": command, "description": description}
                for command, description in commands
            ]
        },
    )
