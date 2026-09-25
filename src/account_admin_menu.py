"""Control-bot screen for people who may command one account."""

from __future__ import annotations

import logging
from html import escape

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from src.database import user_is_account_owner
from src.runtime.plugins import command_prefix
from src.runtime.store import (
    MAX_ACCOUNT_ADMINS,
    add_account_admin,
    list_account_admins,
    remove_account_admin,
)
from src.translation import get_translation_func_for_user

log = logging.getLogger(__name__)

_CANCEL = {"cancel", "الغاء", "إلغاء"}


def _user_id(update: Update) -> int | None:
    user = update.effective_user
    if user is None:
        return None
    return user.id


def _person(row: dict) -> str:
    username = row.get("username")
    if username:
        return f"@{username} ({row['telegram_id']})"
    return str(row["telegram_id"])


async def show_account_admins(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    account_id: int,
) -> None:
    del context
    query = update.callback_query
    user_id = _user_id(update)
    if user_id is None or query is None or query.message is None:
        return
    _ = get_translation_func_for_user(user_id)
    if not user_is_account_owner(account_id, user_id):
        await query.answer(_("Only the account owner can manage command admins."), show_alert=True)
        return
    prefix = escape(command_prefix(account_id))
    rows = list_account_admins(account_id)
    listing = (
        _("No admins yet. Add someone you trust with the button below.")
        if not rows
        else "\n".join(f"● {escape(_person(row))}" for row in rows)
    )
    text = _(
        "<b>👥 Command admins</b>\n"
        "These people can send this account's commands from their own Telegram account, "
        "in a private chat with the account or in a group you are both in.\n"
        "You and the account itself can always send commands. An admin cannot add another admin.\n"
        "Sensitive commands stay with you: profile, password, creating chats, broadcast, "
        "deleting saved replies, and managing admins.\n"
        "From the account you can also send <code>{prefix}رفع ادمن</code> "
        "and <code>{prefix}الادمنية</code>.\n\n"
        "{listing}"
    ).format(prefix=prefix, listing=listing)
    buttons: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(
                _("➕ Add an admin"),
                callback_data=f"mng_admadd_{account_id}",
            )
        ]
    ]
    for row in rows:
        buttons.append(
            [
                InlineKeyboardButton(
                    _("🗑 Remove {name}").format(name=_person(row))[:60],
                    callback_data=f"mng_admrm_{account_id}_{row['telegram_id']}",
                )
            ]
        )
    buttons.append(
        [
            InlineKeyboardButton(
                _("🔙 Back to this account"),
                callback_data=f"mng_select_{account_id}",
            )
        ]
    )
    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(buttons),
        parse_mode=ParseMode.HTML,
    )


async def start_add_account_admin(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    account_id: int,
) -> None:
    query = update.callback_query
    user_id = _user_id(update)
    if user_id is None or query is None or query.message is None:
        return
    _ = get_translation_func_for_user(user_id)
    if not user_is_account_owner(account_id, user_id):
        await query.answer(_("Only the account owner can manage command admins."), show_alert=True)
        return
    user_data = context.user_data
    if user_data is None:
        await query.answer(_("Could not add that admin."), show_alert=True)
        return
    user_data["account_admin_add"] = account_id
    prefix = escape(command_prefix(account_id))
    await query.edit_message_text(
        _(
            "<b>➕ Add an admin</b>\n"
            "Send their numeric Telegram id, or their @username.\n"
            "If the username cannot be found, send the numeric id, or add them from the account "
            "by replying to their message with <code>{prefix}رفع ادمن</code>.\n"
            "Send الغاء to stop.\n"
            "The limit is {limit} admins."
        ).format(prefix=prefix, limit=MAX_ACCOUNT_ADMINS),
        reply_markup=InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        _("✖ Cancel"),
                        callback_data=f"mng_admstop_{account_id}",
                    )
                ]
            ]
        ),
        parse_mode=ParseMode.HTML,
    )


async def cancel_add_account_admin(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    account_id: int,
) -> None:
    user_data = context.user_data
    if user_data is not None:
        user_data.pop("account_admin_add", None)
    await show_account_admins(update, context, account_id)


async def remove_listed_admin(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    account_id: int,
    telegram_id: int,
) -> None:
    query = update.callback_query
    user_id = _user_id(update)
    if user_id is None or query is None:
        return
    _ = get_translation_func_for_user(user_id)
    if not user_is_account_owner(account_id, user_id):
        await query.answer(_("Only the account owner can manage command admins."), show_alert=True)
        return
    if remove_account_admin(account_id, telegram_id):
        await query.answer(_("Admin removed."), show_alert=True)
    else:
        await query.answer(_("That person is not an admin."), show_alert=True)
    await show_account_admins(update, context, account_id)


async def _resolve(bot: object, token: str) -> tuple[int | None, str | None]:
    text = token.strip()
    if not text:
        return None, None
    if text.lstrip("-").isdigit() and not text.startswith("-"):
        return int(text), None
    username = text if text.startswith("@") else f"@{text}"
    if len(username) < 2:
        return None, None
    get_chat = getattr(bot, "get_chat", None)
    if get_chat is None:
        return None, None
    try:
        chat = await get_chat(username)
    except Exception:
        log.debug("Control bot could not resolve %s", username)
        return None, None
    user_id = getattr(chat, "id", None)
    if user_id is None or int(user_id) <= 0:
        return None, None
    found = getattr(chat, "username", None)
    return int(user_id), str(found) if found else username[1:]


async def receive_account_admin_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_data = context.user_data
    pending = user_data.get("account_admin_add") if user_data else None
    if not pending or user_data is None or update.message is None or update.effective_user is None:
        return
    user_id = update.effective_user.id
    _ = get_translation_func_for_user(user_id)
    account_id = int(pending)
    text = (update.message.text or "").strip()
    if text.casefold() in _CANCEL or text in _CANCEL:
        user_data.pop("account_admin_add", None)
        await update.message.reply_text(_("Cancelled."))
        return
    if not user_is_account_owner(account_id, user_id):
        user_data.pop("account_admin_add", None)
        await update.message.reply_text(_("Only the account owner can manage command admins."))
        return
    target_id, username = await _resolve(context.bot, text)
    if target_id is None:
        await update.message.reply_text(
            _(
                "That user was not found. Send a numeric Telegram id, "
                "or add them from the account by reply."
            )
        )
        return
    result = add_account_admin(
        account_id,
        target_id,
        username=username,
        added_by_telegram_id=user_id,
    )
    messages = {
        "added": _("Admin added. They can send commands from their own account."),
        "exists": _("That person is already an admin."),
        "owner": _("The account owner can already send commands."),
        "full": _("This account already has {limit} admins.").format(limit=MAX_ACCOUNT_ADMINS),
        "invalid": _("That id is not valid."),
        "missing": _("Could not add that admin."),
    }
    user_data.pop("account_admin_add", None)
    await update.message.reply_text(messages.get(result, messages["missing"]))
