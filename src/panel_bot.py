"""Control-bot inline query and callback handlers for the account panel."""

from __future__ import annotations

import logging

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InlineQueryResultArticle,
    InputTextMessageContent,
    Update,
)
from telegram.constants import ParseMode
from telegram.ext import CallbackQueryHandler, ContextTypes, InlineQueryHandler

from src.panel import (
    actor_allowed,
    adjust_setting,
    alert_text,
    remove_panel_admin,
    render,
    toggle_plugin,
    unpack_callback,
    unpack_query,
)
from src.templates import panel_denied, panel_expired

log = logging.getLogger(__name__)


def _language(account_id: int) -> str:
    from src.runtime.store import get_plugin_account

    account = get_plugin_account(account_id)
    if account is None:
        return "ar"
    code = str(account.get("language_code") or "ar")
    return "ar" if code == "ar" else "en"


def _markup(rows: list[list[tuple[str, str]]]) -> InlineKeyboardMarkup:
    """Build a keyboard. An empty keyboard removes buttons, including on inline messages."""
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(label, callback_data=data) for label, data in row] for row in rows]
    )


async def on_inline_query(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.inline_query
    if query is None or query.from_user is None:
        return
    parsed = unpack_query(query.query)
    if parsed is None:
        await query.answer([], cache_time=1, is_personal=True)
        return
    account_id, account_user_id = parsed
    if not actor_allowed(account_id, query.from_user.id, account_user_id):
        await query.answer([], cache_time=1, is_personal=True)
        return
    language = _language(account_id)
    text, rows = render(account_id, account_user_id, "h")
    title = "لوحة التحكم" if language == "ar" else "Control panel"
    description = "الأوامر والإضافات" if language == "ar" else "Commands and plugins"
    article = InlineQueryResultArticle(
        id=f"home-{account_id}",
        title=title,
        description=description,
        input_message_content=InputTextMessageContent(
            text,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        ),
        reply_markup=_markup(rows),
    )
    await query.answer([article], cache_time=0, is_personal=True)


async def on_panel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None or query.from_user is None or not query.data:
        return
    parsed = unpack_callback(query.data)
    if parsed is None:
        await query.answer(panel_expired("ar"), show_alert=True)
        return
    language = _language(parsed.account_id)
    if not actor_allowed(parsed.account_id, query.from_user.id, parsed.account_user_id):
        await query.answer(panel_denied(language), show_alert=True)
        return
    notice = ""
    op = parsed.op
    arg = parsed.arg
    if op == "t":
        notice = alert_text(
            language,
            toggle_plugin(parsed.account_id, query.from_user.id, parsed.account_user_id, arg),
        )
        op, arg = "g", parsed.arg
    elif op == "k":
        plugin_name, index_text, action = (arg.rsplit(":", 2) + ["", ""])[:3]
        if index_text.isdigit() and action:
            notice = alert_text(
                language,
                adjust_setting(
                    parsed.account_id,
                    query.from_user.id,
                    parsed.account_user_id,
                    plugin_name,
                    int(index_text),
                    action,
                ),
            )
        op, arg = "s", plugin_name
    elif op == "r":
        notice = alert_text(
            language,
            remove_panel_admin(
                parsed.account_id,
                query.from_user.id,
                parsed.account_user_id,
                arg,
            ),
        )
        op, arg = "a", ""
    try:
        text, rows = render(parsed.account_id, parsed.account_user_id, op, arg)
        # Inline results arrive with inline_message_id and message=None.
        # edit_message_text edits either kind and applies reply_markup to both.
        if query.message is not None or query.inline_message_id:
            await query.edit_message_text(
                text,
                reply_markup=_markup(rows),
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
            )
    except Exception as exc:
        log.warning("Panel edit failed: %s", exc, exc_info=True)
    if notice:
        await query.answer(notice, show_alert=True)
        return
    await query.answer()


def panel_handlers() -> list:
    return [
        InlineQueryHandler(on_inline_query),
        CallbackQueryHandler(on_panel_callback, pattern=r"^p\."),
    ]
