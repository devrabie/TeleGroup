"""Team managers and add-number invite links."""

import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from src.database import (
    SHARE_KIND_ADD,
    SHARE_KIND_TEAM,
    add_account_manager,
    get_or_create_sharing_token,
    get_user_by_username,
    list_account_managers,
    list_owners_for_manager,
    remove_account_manager,
    rotate_sharing_token,
)
from src.translation import get_translation_func_for_user

log = logging.getLogger(__name__)

TEAM_WAIT_ID = 20


def format_person(telegram_id: int, first_name: str | None = None, username: str | None = None) -> str:
    """Human-readable label for a Telegram user (notifications and lists)."""
    name = (first_name or "").strip() or str(telegram_id)
    if username:
        return f"{name} (@{username})"
    return f"{name} (ID: {telegram_id})"


async def bot_username(context: ContextTypes.DEFAULT_TYPE) -> str | None:
    cached = context.bot_data.get("bot_username")
    if cached:
        return cached
    me = await context.bot.get_me()
    if not me.username:
        return None
    context.bot_data["bot_username"] = me.username
    return me.username


def deep_link(username: str, kind: str, token: str) -> str:
    return f"https://t.me/{username}?start={kind}_{token}"


async def _send_or_edit(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str, reply_markup) -> None:
    query = update.callback_query
    if query and query.message:
        await query.edit_message_text(
            text,
            reply_markup=reply_markup,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
        return
    message = update.effective_message
    await message.reply_text(
        text,
        reply_markup=reply_markup,
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
    )


async def show_team_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    user_id = user.id
    _ = get_translation_func_for_user(user_id)
    managers = list_account_managers(user_id)
    managed_for = list_owners_for_manager(user_id)
    username = await bot_username(context)
    token = get_or_create_sharing_token(user_id, SHARE_KIND_TEAM)
    team_link = deep_link(username, SHARE_KIND_TEAM, token) if username and token else None

    lines = [
        _("<b>Team</b>"),
        "",
        _("Managers can view and manage every Telegram account you added. They cannot change this team or your invite links."),
    ]

    if managers:
        lines.append("")
        lines.append(_("<b>Managers</b>"))
        for manager in managers:
            lines.append(
                "• " + format_person(
                    manager["manager_telegram_id"],
                    manager.get("first_name"),
                    manager.get("username"),
                )
            )
    else:
        lines.append("")
        lines.append(_("No managers yet."))

    if team_link:
        lines.append("")
        lines.append(_("Share this link so someone can become a manager:"))
        lines.append(f"<code>{team_link}</code>")
    else:
        lines.append("")
        lines.append(_("Could not build the team link. Make sure the bot has a public username."))

    if managed_for:
        lines.append("")
        lines.append(_("<b>You manage accounts for</b>"))
        for owner in managed_for:
            lines.append("• " + format_person(owner["telegram_id"], owner.get("first_name"), owner.get("username")))
        lines.append("")
        lines.append(_("To add a number to their list, ask them for their add-number invite link."))

    buttons = [
        [InlineKeyboardButton(_("➕ Add manager by ID"), callback_data="team_add")],
    ]
    for manager in managers:
        label = format_person(
            manager["manager_telegram_id"],
            manager.get("first_name"),
            manager.get("username"),
        )
        if len(label) > 40:
            label = label[:39] + "…"
        buttons.append([
            InlineKeyboardButton(
                _("❌ Remove {name}").format(name=label),
                callback_data=f"team_remove_{manager['manager_telegram_id']}",
            )
        ])
    buttons.append([InlineKeyboardButton(_("🔄 New team link"), callback_data="team_rotate")])
    buttons.append([InlineKeyboardButton(_("🔙 Back"), callback_data="main_back")])

    await _send_or_edit(update, context, "\n".join(lines), InlineKeyboardMarkup(buttons))


async def show_invite_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    _ = get_translation_func_for_user(user_id)
    username = await bot_username(context)
    token = get_or_create_sharing_token(user_id, SHARE_KIND_ADD)
    add_link = deep_link(username, SHARE_KIND_ADD, token) if username and token else None

    lines = [
        _("<b>Add-number invite</b>"),
        "",
        _(
            "Anyone who opens this link can sign in a phone number. "
            "The number is added to <b>your</b> account list, it does not appear in theirs, "
            "and you get a notification."
        ),
        "",
        _("They do not need their own subscription. The number counts against your plan limit."),
    ]
    if add_link:
        lines.append("")
        lines.append(_("Your invite link:"))
        lines.append(f"<code>{add_link}</code>")
    else:
        lines.append("")
        lines.append(_("Could not build the invite link. Make sure the bot has a public username."))

    buttons = [
        [InlineKeyboardButton(_("🔄 New invite link"), callback_data="invite_rotate")],
        [InlineKeyboardButton(_("🔙 Back"), callback_data="main_back")],
    ]
    await _send_or_edit(update, context, "\n".join(lines), InlineKeyboardMarkup(buttons))


async def team_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user_id = query.from_user.id
    _ = get_translation_func_for_user(user_id)
    data = query.data or ""

    if data == "team_rotate":
        token = rotate_sharing_token(user_id, SHARE_KIND_TEAM)
        if token:
            await query.answer(_("New team link created. The old one no longer works."), show_alert=True)
        else:
            await query.answer(_("Could not create a new link."), show_alert=True)
        await show_team_menu(update, context)
        return

    if data.startswith("team_remove_"):
        try:
            manager_id = int(data.rsplit("_", 1)[1])
        except (IndexError, ValueError):
            await query.answer(_("Could not remove that manager."), show_alert=True)
            return
        if remove_account_manager(user_id, manager_id):
            await query.answer(_("Manager removed."))
        else:
            await query.answer(_("Could not remove that manager."), show_alert=True)
        await show_team_menu(update, context)
        return

    await query.answer()
    await show_team_menu(update, context)


async def invite_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user_id = query.from_user.id
    _ = get_translation_func_for_user(user_id)
    data = query.data or ""

    if data == "invite_rotate":
        token = rotate_sharing_token(user_id, SHARE_KIND_ADD)
        if token:
            await query.answer(_("New invite link created. The old one no longer works."), show_alert=True)
        else:
            await query.answer(_("Could not create a new link."), show_alert=True)
        await show_invite_menu(update, context)
        return

    await query.answer()
    await show_invite_menu(update, context)


async def team_add_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    _ = get_translation_func_for_user(query.from_user.id)
    await query.edit_message_text(
        _(
            "Send the manager's numeric Telegram user ID, or their @username if they have already used this bot.\n\n"
            "Use /cancel to go back."
        )
    )
    return TEAM_WAIT_ID


def _parse_manager_input(raw: str):
    text = (raw or "").strip()
    if not text:
        return None, None
    if text.startswith("@"):
        user = get_user_by_username(text)
        if not user:
            return None, "username"
        return user["telegram_id"], user
    if text.isdigit():
        return int(text), None
    user = get_user_by_username(text)
    if user:
        return user["telegram_id"], user
    return None, "invalid"


async def team_add_receive(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    owner = update.effective_user
    _ = get_translation_func_for_user(owner.id)
    manager_id, looked_up = _parse_manager_input(update.message.text)
    if manager_id is None:
        if looked_up == "username":
            await update.message.reply_text(
                _("No bot user with that username. They must press /start first, or send their numeric Telegram ID.")
            )
        else:
            await update.message.reply_text(
                _("Please send a numeric Telegram user ID or an @username.")
            )
        return TEAM_WAIT_ID

    result = add_account_manager(owner.id, manager_id)
    if result == "self":
        await update.message.reply_text(_("You cannot add yourself as a manager."))
    elif result == "exists":
        await update.message.reply_text(_("That person is already a manager."))
    elif result == "ok":
        if looked_up:
            label = format_person(manager_id, looked_up.get("first_name"), looked_up.get("username"))
        else:
            label = format_person(manager_id)
        await update.message.reply_text(
            _("{name} can now view and manage your accounts.").format(name=label)
        )
        try:
            manager_ = get_translation_func_for_user(manager_id)
            await context.bot.send_message(
                manager_id,
                manager_(
                    "You can now view and manage {name}'s accounts from My Accounts. "
                    "The numbers stay on their list, not yours."
                ).format(name=owner.first_name or str(owner.id)),
            )
        except Exception as e:
            log.info(f"Could not notify new manager {manager_id}: {e}")
    else:
        await update.message.reply_text(_("Could not add that manager. Please try again."))

    await show_team_menu(update, context)
    return ConversationHandler.END


async def cancel_team_add(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    _ = get_translation_func_for_user(update.effective_user.id)
    query = update.callback_query
    if query:
        await query.answer()
        await show_team_menu(update, context)
    else:
        await update.message.reply_text(_("Operation cancelled."))
        await show_team_menu(update, context)
    return ConversationHandler.END


team_add_conv_handler = ConversationHandler(
    entry_points=[CallbackQueryHandler(team_add_start, pattern="^team_add$")],
    states={
        TEAM_WAIT_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, team_add_receive)],
    },
    fallbacks=[
        CommandHandler("cancel", cancel_team_add),
        CommandHandler("start", cancel_team_add),
        CallbackQueryHandler(cancel_team_add, pattern="^main_back$"),
        CallbackQueryHandler(cancel_team_add, pattern="^main_team$"),
    ],
    conversation_timeout=300,
    per_message=False,
)
