"""Single inbound Telegram process.

Telegram permits only one `getUpdates` consumer per bot token, so onboarding
(`/start`, `/help`) and stock-confirmation callbacks have to live in the same
application. Outbound sends stay in `bot.telegram_api`, which is synchronous --
`sweep_freshness` is a management command and PTB v22 is async-first.
"""

import logging
import math

from asgiref.sync import sync_to_async
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from telegram import Update
from telegram.ext import (
    Application,
    ApplicationHandlerStop,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    TypeHandler,
    filters,
)

from bot import keyboards, messages, parsing
from bot.selectors import listing_for_chat, shops_for_chat
from bot.services import (
    answer_confirmation_digest,
    answer_digest_selection,
    handle_digest_text,
    handle_product_callback,
    handle_telegram_callback,
    handle_text_reply,
    link_shop_to_chat,
    looks_like_link_code,
    record_update,
    review_next,
    shop_status,
    start_review,
    unlink_chat,
)

logger = logging.getLogger(__name__)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    logger.info(
        "Inbound /start from user %s (chat %s)", update.effective_user.username, chat_id
    )

    code = " ".join(context.args).strip() if context.args else ""
    if code:
        logger.info("Inbound /start with code from chat %s", chat_id)
        await _apply_link(update, chat_id, code)
        return

    # The link code, not the chat ID, is what a merchant now needs: `/link` binds
    # the shop for them. The chat ID stays on the last line because support still
    # asks for it when diagnosing a shop that is prompting the wrong chat.
    text = messages.welcome(chat_id)
    await update.message.reply_text(text=text)
    logger.info("Outbound reply to chat %s: %r", chat_id, text)


async def help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    logger.info(
        "Inbound /help from user %s (chat %s)", update.effective_user.username, chat_id
    )
    text = messages.help_text()
    await update.message.reply_text(text=text)
    logger.info("Outbound reply to chat %s: %r", chat_id, text)


def _render_listing(shops, products, page: int):
    """Build one page of the confirmation listing.

    `page` is clamped rather than rejected: the list shrinks as the merchant
    answers, so a stale page number from an older message is normal, not an error.
    """
    page_size = settings.MYPRODUCTS_PAGE_SIZE
    total = len(products)
    pages = max(1, math.ceil(total / page_size))
    page = max(0, min(page, pages - 1))
    start = page * page_size
    visible = products[start : start + page_size]
    label_shop = len(shops) > 1

    has_restockable = any(not getattr(p, "is_available", True) for p in visible)
    text = messages.listing_summary(
        total, page, pages, has_restockable=has_restockable
    )
    markup = keyboards.listing_markup(
        visible, page=page, pages=pages, label_shop=label_shop
    )
    return text, markup


async def my_products(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """List the products this chat's shops need to confirm."""
    chat_id = update.effective_chat.id
    logger.info("Inbound /myproducts from chat %s", chat_id)

    shops, products = await sync_to_async(listing_for_chat, thread_sensitive=True)(
        chat_id
    )

    if not shops:
        await update.message.reply_text(messages.not_linked())
        return

    if not products:
        await update.message.reply_text(messages.nothing_needs_confirming())
        return

    text, markup = _render_listing(shops, products, page=0)
    await update.message.reply_text(text, reply_markup=markup)


async def _refresh_listing(query, chat_id, page: int):
    """Redraw the listing in place, so the chat does not fill with copies."""
    shops, products = await sync_to_async(listing_for_chat, thread_sensitive=True)(
        chat_id
    )
    if not products:
        await query.edit_message_text(messages.nothing_left_to_confirm())
        return

    text, markup = _render_listing(shops, products, page=page)
    await query.edit_message_text(text, reply_markup=markup)


async def _on_listing_answer(query, chat_id, data: str):
    product = await sync_to_async(handle_product_callback, thread_sensitive=True)(
        callback_data=data,
        chat_id=str(chat_id),
    )
    if product is None:
        # Could be a foreign chat, a deleted product, or ✅ on something already
        # out of stock. The merchant does not need to know which; they need a way
        # forward that does not involve an out-of-date message.
        await query.answer(messages.could_not_record())
        return

    await query.answer(messages.recorded())
    # The answered row leaves the list, so redraw to keep the count honest.
    parsed = parsing.parse_listing_callback(data)
    page = parsed[2] if parsed else 0
    await _refresh_listing(query, chat_id, page=page)


async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Route every inline-button press: listing paging, listing answers, prompts."""
    query = update.callback_query
    if query is None:
        return

    chat_id = (
        query.message.chat.id
        if query.message and query.message.chat
        else (update.effective_chat.id if update.effective_chat else None)
    )
    data = query.data or ""
    logger.info("Inbound callback %r from chat %s", data, chat_id)

    unlink_action = parsing.parse_unlink_callback(data)
    if unlink_action is not None:
        if unlink_action == "yes":
            unlinked_shops = await sync_to_async(unlink_chat, thread_sensitive=True)(
                chat_id
            )
            await query.answer(messages.recorded())
            if query.message:
                await query.edit_message_text(
                    messages.unlink_confirmed(len(unlinked_shops))
                )
        else:
            await query.answer()
            if query.message:
                await query.edit_message_text(messages.unlink_cancelled())
        return

    digest_id = parsing.parse_digest_all_callback(data)
    if digest_id is not None:
        outcome, count = await sync_to_async(
            answer_confirmation_digest, thread_sensitive=True
        )(chat_id=str(chat_id), digest_id=digest_id, available=True)
        if outcome == "not_found":
            await query.answer(messages.request_inactive())
            return
        if outcome == "already_answered":
            await query.answer(messages.response_recorded())
            if query.message:
                await query.edit_message_text(messages.digest_already_done())
            return
        await query.answer(messages.response_recorded())
        if query.message:
            await query.edit_message_text(messages.digest_ack(count))
        return

    digest_review_id = parsing.parse_digest_review_callback(data)
    if digest_review_id is not None:
        outcome, cursor, item, progress = await sync_to_async(
            start_review, thread_sensitive=True
        )(chat_id=str(chat_id), digest_id=digest_review_id)
        if outcome == "not_found":
            await query.answer(messages.request_inactive())
            return
        if outcome == "done":
            await query.answer(messages.response_recorded())
            if query.message:
                confirmed = progress.confirmed if progress else 0
                sold = progress.sold if progress else 0
                await query.edit_message_text(messages.review_done(confirmed, sold))
            return
        if outcome == "item" and item and progress:
            await query.answer()
            if query.message:
                text = messages.review_item(
                    progress.index,
                    progress.total,
                    item.label,
                    item.price_str,
                    item.age_days,
                )
                markup = keyboards.review_markup(item.request_id)
                await query.edit_message_text(text, reply_markup=markup)
            return

    review_action = parsing.parse_review_action_callback(data)
    if review_action is not None:
        request_id, action = review_action
        outcome, cursor, item, progress = await sync_to_async(
            review_next, thread_sensitive=True
        )(chat_id=str(chat_id), digest_id=None, action=action, request_id=request_id)
        if outcome == "not_found":
            await query.answer(messages.request_inactive())
            return
        if outcome == "paused":
            await query.answer()
            if query.message:
                await query.edit_message_text(messages.review_paused())
            return
        if outcome == "done":
            await query.answer(messages.response_recorded())
            if query.message:
                confirmed = progress.confirmed if progress else 0
                sold = progress.sold if progress else 0
                await query.edit_message_text(messages.review_done(confirmed, sold))
            return
        if outcome == "item" and item and progress:
            await query.answer()
            if query.message:
                text = messages.review_item(
                    progress.index,
                    progress.total,
                    item.label,
                    item.price_str,
                    item.age_days,
                )
                markup = keyboards.review_markup(item.request_id)
                await query.edit_message_text(text, reply_markup=markup)
            return

    numeric_sel = parsing.parse_numeric_selection_callback(data)
    if numeric_sel is not None:
        digest_id, action, indices = numeric_sel
        available = action == "a"
        result = await sync_to_async(answer_digest_selection, thread_sensitive=True)(
            chat_id=str(chat_id),
            digest_id=digest_id,
            available=available,
            indices=indices,
        )
        outcome, count = result[0], result[1]
        if outcome == "not_found":
            await query.answer(messages.request_inactive())
            return
        if outcome == "already_answered":
            await query.answer(messages.response_recorded())
            if query.message:
                await query.edit_message_text(messages.already_recorded())
            return

        await query.answer(messages.response_recorded())
        if query.message:
            if available:
                if result.remaining == 0:
                    text = messages.digest_ack(count)
                else:
                    p_str = "1 product is" if count == 1 else f"{count} products are"
                    rem_str = (
                        "1 product is"
                        if result.remaining == 1
                        else f"{result.remaining} products are"
                    )
                    text = f"✅ Thanks! {p_str} confirmed in stock.\n{rem_str} still waiting on you."
            else:
                text = messages.digest_sold_ack(result.labels, result.remaining)
            await query.edit_message_text(text)
        return

    # /myproducts buttons are colon-separated and product-keyed; the original
    # inline prompts are underscore-separated and request-keyed. Route on that.
    page = parsing.parse_page_callback(data)
    if page is not None:
        await query.answer()
        await _refresh_listing(query, chat_id, page=page)
        return
    if parsing.parse_listing_callback(data) is not None:
        await _on_listing_answer(query, chat_id, data)
        return

    # The service owns authorisation and the state change. It is handed only the
    # data and the chat: replying to Telegram is PTB's job here, so we withhold
    # callback_query_id/message_id rather than trigger the sync transport too.
    outcome = await sync_to_async(handle_telegram_callback, thread_sensitive=True)(
        callback_data=data,
        chat_id=str(chat_id),
    )

    if outcome == "not_found":
        await query.answer(messages.request_inactive())
        return

    if outcome == "already_answered":
        await query.answer(messages.response_recorded())
        if query.message:
            await query.edit_message_text(messages.already_recorded())
        return

    parsed_prompt = parsing.parse_prompt_callback(data)
    action = parsed_prompt[0] if parsed_prompt else ""
    await query.answer(messages.response_recorded())
    if query.message:
        await query.edit_message_text(
            messages.callback_confirm_ack(action == "confirm")
        )


def _link_ack(outcome: str, shop) -> str:
    """One short line per link outcome. Every failure names a way forward."""
    shop_name = shop.name if shop else None
    return messages.link_ack(outcome, shop_name)


async def _apply_link(update: Update, chat_id, code: str):
    """Spend a code and report the outcome. Shared by /link and a bare paste."""
    outcome, shop = await sync_to_async(link_shop_to_chat, thread_sensitive=True)(
        code=code, chat_id=str(chat_id)
    )
    await update.message.reply_text(_link_ack(outcome, shop))


async def link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Bind this chat to a shop using a one-time code issued in the admin."""
    chat_id = update.effective_chat.id
    # The code itself is never logged: it is a bearer credential until spent.
    logger.info("Inbound /link from chat %s", chat_id)

    argument = " ".join(context.args) if context.args else ""
    if not argument:
        await update.message.reply_text(messages.link_usage())
        return

    await _apply_link(update, chat_id, argument)


async def unlink(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Offer to unlink this chat from its shop(s)."""
    chat_id = update.effective_chat.id
    logger.info("Inbound /unlink from chat %s", chat_id)

    shops = await sync_to_async(shops_for_chat, thread_sensitive=True)(chat_id)
    if not shops:
        await update.message.reply_text(messages.not_linked())
        return

    text = messages.unlink_prompt(len(shops))
    markup = keyboards.unlink_confirmation_markup()
    await update.message.reply_text(text=text, reply_markup=markup)


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """View listing freshness and last check-in for linked shop(s)."""
    chat_id = update.effective_chat.id
    logger.info("Inbound /status from chat %s", chat_id)

    shops = await sync_to_async(shops_for_chat, thread_sensitive=True)(chat_id)
    if not shops:
        await update.message.reply_text(messages.not_linked())
        return

    prefix_shop = len(shops) > 1
    lines = []
    for shop in shops:
        data = await sync_to_async(shop_status, thread_sensitive=True)(shop)
        lines.append(
            messages.status(
                shop_name=shop.name,
                fresh=data["fresh"],
                due=data["due"],
                hidden=data["hidden"],
                last_checkin=data["last_checkin"],
                prefix_shop=prefix_shop,
            )
        )
    await update.message.reply_text("\n".join(lines))


def _text_reply_ack(outcome: str, product) -> str:
    """One short line per outcome. Every failure names a way forward."""
    product_name = product.name if product else None
    return messages.text_reply_ack(outcome, product_name)


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Apply a free-text answer, or explain why it could not be applied."""
    message = update.message
    if message is None or not message.text:
        return

    chat_id = update.effective_chat.id
    reply_to = message.reply_to_message.message_id if message.reply_to_message else None

    # A merchant who pastes the bare code without the command still gets linked.
    # Checked first, and only for text already shaped like a code, so the yes/no
    # path below is untouched. Logged before the text is, so the code never
    # reaches the log line underneath.
    if looks_like_link_code(message.text):
        logger.info("Inbound bare link code from chat %s", chat_id)
        await _apply_link(update, chat_id, message.text)
        return

    logger.info(
        "Inbound text %r from chat %s (reply_to=%s)", message.text, chat_id, reply_to
    )

    digest_res = await sync_to_async(handle_digest_text, thread_sensitive=True)(
        chat_id=str(chat_id),
        text=message.text,
        reply_to_message_id=reply_to,
    )
    if digest_res is not None:
        res_type = digest_res[0]
        if res_type == "all_available":
            _type, _digest_id, count = digest_res
            await message.reply_text(messages.digest_ack(count))
            return
        if res_type == "sold":
            _type, names, remaining = digest_res
            await message.reply_text(messages.digest_sold_ack(names, remaining))
            return
        if res_type == "ambiguous":
            _type, digest_id, indices = digest_res
            await message.reply_text(
                messages.digest_ambiguous(indices),
                reply_markup=keyboards.digest_ambiguous_markup(digest_id, indices),
            )
            return
        if res_type == "already_answered":
            await message.reply_text(messages.already_recorded())
            return
        if res_type == "unparsed":
            _type, digest_id = digest_res
            await message.reply_text(
                messages.digest_unparsed(),
                reply_markup=keyboards.digest_markup(digest_id),
            )
            return

    outcome, product = await sync_to_async(handle_text_reply, thread_sensitive=True)(
        text=message.text,
        chat_id=str(chat_id),
        reply_to_message_id=reply_to,
    )
    await message.reply_text(_text_reply_ack(outcome, product))


async def dedupe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Drop redelivered updates before they reach any business handlers."""
    if not hasattr(update, "update_id") or update.update_id is None:
        return
    fresh = await sync_to_async(record_update, thread_sensitive=True)(update.update_id)
    if not fresh:
        logger.info("Duplicate update %s dropped.", update.update_id)
        raise ApplicationHandlerStop


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE):
    """Global error handler. Logs exceptions and informs the user if possible."""
    error = context.error
    update_id = getattr(update, "update_id", None) if update else None
    chat_id = None
    if update and hasattr(update, "effective_chat") and update.effective_chat:
        chat_id = update.effective_chat.id

    error_type = type(error).__name__ if error else "UnknownError"
    logger.exception(
        "Exception while handling update %s (chat %s): %s: %s",
        update_id,
        chat_id,
        error_type,
        error,
        exc_info=error,
    )

    if update and hasattr(update, "effective_message") and update.effective_message:
        try:
            await update.effective_message.reply_text(messages.generic_error())
        except Exception as exc:
            logger.warning("Failed to send error reply to chat %s: %s", chat_id, exc)


class Command(BaseCommand):
    help = (
        "Runs the Telegram listener: onboarding, shop linking, /myproducts, "
        "and stock callbacks."
    )

    def handle(self, *args, **options):
        token = settings.TELEGRAM_BOT_TOKEN
        if not token:
            raise CommandError(
                "TELEGRAM_BOT_TOKEN is not set. Add it to your .env file."
            )

        app = Application.builder().token(token).build()
        app.add_error_handler(on_error)
        app.add_handler(TypeHandler(Update, dedupe), group=-1)
        app.add_handler(CommandHandler("start", start))
        app.add_handler(CommandHandler("help", help))
        app.add_handler(CommandHandler("link", link))
        app.add_handler(CommandHandler("unlink", unlink))
        app.add_handler(CommandHandler("status", status))
        app.add_handler(CommandHandler("myproducts", my_products))
        app.add_handler(CallbackQueryHandler(on_callback))
        # Last, and command-free: a merchant who ignores the buttons and just
        # types "sold out" still gets an answer recorded.
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))

        logger.info("Starting Telegram long-polling...")
        app.run_polling()
