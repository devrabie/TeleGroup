"""Account-detail screens: profile, private inbox, and conversation history."""

from __future__ import annotations

import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.error import BadRequest
from telegram.ext import ContextTypes

from src.account_explorer import (
    INBOX_PAGE_SIZE,
    MESSAGE_PAGE_SIZE,
    ExplorerError,
    dialog_title,
    display_name,
    fetch_private_dialogs,
    fetch_private_messages,
    fetch_profile,
    format_dialog_button,
    format_message_line,
    format_profile_text,
    get_cached_identity,
    paginate,
    trim_html,
)
from src.database import get_account_details, user_owns_account
from src.translation import get_translation_func_for_user

log = logging.getLogger(__name__)


async def _safe_edit(bot, *, chat_id, message_id, text, reply_markup=None, parse_mode=None):
    try:
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=text,
            reply_markup=reply_markup,
            parse_mode=parse_mode,
        )
        return True
    except BadRequest as e:
        if "not modified" in str(e).lower():
            return False
        raise


def explorer_error_text(error: ExplorerError, _) -> str:
    if error.code == "session_invalid":
        return _("The session is invalid or revoked. Please re-add the account.")
    if error.code in {"connect_failed", "account_unavailable"}:
        return _("This account could not connect. Check the proxy and try again.")
    if error.code == "flood_wait":
        return _("Telegram asked us to wait. Please try again in a moment.")
    return _("An unexpected error occurred while loading this page.")


def owned_account(account_id: int, telegram_user_id: int) -> bool:
    return bool(user_owns_account(account_id, telegram_user_id))


async def load_identity_for_menu(account_id: int, account: dict | None = None) -> dict | None:
    """Return a cached Telegram name only. Never connect here — that blocks buttons."""
    return get_cached_identity(account_id)


async def show_profile(update, context: ContextTypes.DEFAULT_TYPE, account_id: int, *, force: bool = False):
    query = update.callback_query
    user_id = query.from_user.id
    _ = get_translation_func_for_user(user_id)
    if not owned_account(account_id, user_id):
        try:
            await query.answer(_("Error: Account not found or you don't have permission."), show_alert=True)
        except BadRequest:
            pass
        return

    await _safe_edit(
        context.bot,
        chat_id=user_id,
        message_id=query.message.message_id,
        text=_("Loading profile…"),
    )
    acc = get_account_details(account_id)
    phone = acc["phone"] if acc else ""
    try:
        from src.code_monitor import code_monitor_manager
        code_monitor_manager.set_bot(context.bot)
        profile = await fetch_profile(account_id, force=force)
    except ExplorerError as e:
        buttons = [[InlineKeyboardButton(_("🔙 Back to Account"), callback_data=f"mng_select_{account_id}")]]
        await _safe_edit(
            context.bot,
            chat_id=user_id,
            message_id=query.message.message_id,
            text=explorer_error_text(e, _),
            reply_markup=InlineKeyboardMarkup(buttons),
        )
        return

    text = format_profile_text(profile, phone, _)
    buttons = [
        [
            InlineKeyboardButton(_("🔄 Refresh"), callback_data=f"mng_prefresh_{account_id}"),
            InlineKeyboardButton(_("💬 Private Chats"), callback_data=f"mng_inbox_{account_id}"),
        ],
        [InlineKeyboardButton(_("🔙 Back to Account"), callback_data=f"mng_select_{account_id}")],
    ]
    await _safe_edit(
        context.bot,
        chat_id=user_id,
        message_id=query.message.message_id,
        text=text,
        reply_markup=InlineKeyboardMarkup(buttons),
        parse_mode=ParseMode.HTML,
    )


async def show_inbox(
    update,
    context: ContextTypes.DEFAULT_TYPE,
    account_id: int,
    page: int = 0,
    *,
    force: bool = False,
):
    query = update.callback_query
    user_id = query.from_user.id
    _ = get_translation_func_for_user(user_id)
    if not owned_account(account_id, user_id):
        try:
            await query.answer(_("Error: Account not found or you don't have permission."), show_alert=True)
        except BadRequest:
            pass
        return

    await _safe_edit(
        context.bot,
        chat_id=user_id,
        message_id=query.message.message_id,
        text=_("Loading private chats…"),
    )
    acc = get_account_details(account_id)
    phone = acc["phone"] if acc else ""
    try:
        from src.code_monitor import code_monitor_manager
        code_monitor_manager.set_bot(context.bot)
        dialogs = await fetch_private_dialogs(account_id, force=force)
        identity = get_cached_identity(account_id)
    except ExplorerError as e:
        buttons = [[InlineKeyboardButton(_("🔙 Back to Account"), callback_data=f"mng_select_{account_id}")]]
        await _safe_edit(
            context.bot,
            chat_id=user_id,
            message_id=query.message.message_id,
            text=explorer_error_text(e, _),
            reply_markup=InlineKeyboardMarkup(buttons),
        )
        return

    saved_label = _("💾 Saved Messages")
    page_items, page, pages = paginate(dialogs, page, INBOX_PAGE_SIZE)
    owner_name = escape_name(display_name(identity, phone))
    text = _("💬 <b>Private chats</b> · {name}\n{count} conversations · page {page}/{pages}").format(
        name=owner_name,
        count=len(dialogs),
        page=page + 1,
        pages=pages,
    )
    if not dialogs:
        text += "\n\n" + _("This account has no private conversations.")

    buttons = []
    for dialog in page_items:
        buttons.append([
            InlineKeyboardButton(
                format_dialog_button(dialog, saved_label),
                callback_data=f"mng_dm_{account_id}_{dialog['chat_id']}_0",
            )
        ])

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(_("⬅️ Previous"), callback_data=f"mng_inbox_{account_id}_{page - 1}"))
    if page + 1 < pages:
        nav.append(InlineKeyboardButton(_("Next ➡️"), callback_data=f"mng_inbox_{account_id}_{page + 1}"))
    if nav:
        buttons.append(nav)
    buttons.append([
        InlineKeyboardButton(_("🔄 Refresh"), callback_data=f"mng_irefresh_{account_id}"),
        InlineKeyboardButton(_("🔙 Back to Account"), callback_data=f"mng_select_{account_id}"),
    ])
    await _safe_edit(
        context.bot,
        chat_id=user_id,
        message_id=query.message.message_id,
        text=trim_html(text),
        reply_markup=InlineKeyboardMarkup(buttons),
        parse_mode=ParseMode.HTML,
    )


async def show_conversation(
    update,
    context: ContextTypes.DEFAULT_TYPE,
    account_id: int,
    chat_id: int,
    page: int = 0,
    *,
    force: bool = False,
):
    query = update.callback_query
    user_id = query.from_user.id
    _ = get_translation_func_for_user(user_id)
    if not owned_account(account_id, user_id):
        try:
            await query.answer(_("Error: Account not found or you don't have permission."), show_alert=True)
        except BadRequest:
            pass
        return

    await _safe_edit(
        context.bot,
        chat_id=user_id,
        message_id=query.message.message_id,
        text=_("Opening conversation…"),
    )
    try:
        payload = await fetch_private_messages(account_id, chat_id, force=force)
    except ExplorerError as e:
        buttons = [[InlineKeyboardButton(_("🔙 Back to Chats"), callback_data=f"mng_inbox_{account_id}")]]
        await _safe_edit(
            context.bot,
            chat_id=user_id,
            message_id=query.message.message_id,
            text=explorer_error_text(e, _),
            reply_markup=InlineKeyboardMarkup(buttons),
        )
        return

    chat = payload.get("chat") or {}
    messages = payload.get("messages") or []
    page_items, page, pages = paginate(messages, page, MESSAGE_PAGE_SIZE)
    saved_label = _("💾 Saved Messages")
    title = escape_name(dialog_title({**chat, "is_official": chat.get("is_official")}, saved_label))
    subtitle_bits = []
    if chat.get("username"):
        subtitle_bits.append(f"@{chat['username']}")
    subtitle_bits.append(f"ID {chat_id}")
    text = _("💬 <b>{name}</b>\n{subtitle}\n").format(
        name=title,
        subtitle=escape_name(" · ".join(subtitle_bits)),
    )
    if not messages:
        text += "\n" + _("No messages in this conversation.")
    else:
        you_label = _("You")
        lines = [format_message_line(item, _, you_label) for item in page_items]
        text += "\n" + "\n\n".join(lines)
        text += "\n\n" + _("Page {page}/{pages}").format(page=page + 1, pages=pages)

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(_("⬅️ Newer"), callback_data=f"mng_dm_{account_id}_{chat_id}_{page - 1}"))
    if page + 1 < pages:
        nav.append(InlineKeyboardButton(_("Older ➡️"), callback_data=f"mng_dm_{account_id}_{chat_id}_{page + 1}"))
    buttons = [nav] if nav else []
    buttons.append([
        InlineKeyboardButton(_("🔄 Refresh"), callback_data=f"mng_drefresh_{account_id}_{chat_id}"),
        InlineKeyboardButton(_("🔙 Back to Chats"), callback_data=f"mng_inbox_{account_id}"),
    ])
    await _safe_edit(
        context.bot,
        chat_id=user_id,
        message_id=query.message.message_id,
        text=trim_html(text),
        reply_markup=InlineKeyboardMarkup(buttons),
        parse_mode=ParseMode.HTML,
    )


def escape_name(value: str) -> str:
    from html import escape
    return escape(value or "")
