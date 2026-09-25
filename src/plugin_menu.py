"""Control-bot screens for per-account plugins and per-plan allowlists."""

from __future__ import annotations

import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from src.database import get_user_language, user_owns_account
from src.runtime.gating import list_plan_plugin_views, list_plugin_views, set_account_plugin
from src.runtime.plugins import command_prefix
from src.runtime.store import set_plan_plugin_allowed
from src.translation import get_translation_func_for_user

log = logging.getLogger(__name__)


def _mark(enabled: bool) -> str:
    return "🟢" if enabled else "⚪️"


def _user_id(update: Update) -> int | None:
    user = update.effective_user
    if user is None:
        return None
    return user.id


async def show_plugins_menu(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    account_id: int,
) -> None:
    query = update.callback_query
    user_id = _user_id(update)
    if user_id is None:
        return
    _ = get_translation_func_for_user(user_id)
    if query is None or query.message is None:
        return
    if not user_owns_account(account_id, user_id):
        await query.answer(
            _("Error: Account not found or you don't have permission."), show_alert=True
        )
        return
    language = get_user_language(user_id)
    views = list_plugin_views(account_id, language)
    if views is None:
        await query.answer(
            _("Error: Account not found or you don't have permission."), show_alert=True
        )
        return
    prefix = command_prefix(account_id)
    lines = [
        _(
            "<b>Plugins</b>\n"
            "Choose which features this account runs. "
            "Locked plugins are not included in the current plan."
        )
    ]
    buttons: list[list[InlineKeyboardButton]] = []
    for view in views:
        if not view["allowed"]:
            state = _("Not in your plan")
            mark = "🔒"
        elif view["enabled"]:
            state = _("🟢 On")
            mark = _mark(True)
        else:
            state = _("⚪️ Off")
            mark = _mark(False)
        lines.append(f"\n{mark} <b>{view['name']}</b> — {state}\n{view['description']}")
        if view["commands"]:
            lines.append(" ".join(f"<code>{prefix}{name}</code>" for name in view["commands"]))
        buttons.append(
            [
                InlineKeyboardButton(
                    f"{mark} {view['name']}",
                    callback_data=f"mng_plugtog_{account_id}_{view['name']}",
                )
            ]
        )
    buttons.append(
        [InlineKeyboardButton(_("🔙 Back to Account"), callback_data=f"mng_select_{account_id}")]
    )
    await query.edit_message_text(
        "\n".join(lines),
        reply_markup=InlineKeyboardMarkup(buttons),
        parse_mode=ParseMode.HTML,
    )


async def toggle_account_plugin(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    account_id: int,
    plugin_name: str,
) -> None:
    query = update.callback_query
    user_id = _user_id(update)
    if user_id is None:
        return
    _ = get_translation_func_for_user(user_id)
    if query is None:
        return
    result = set_account_plugin(account_id, user_id, plugin_name)
    messages = {
        "enabled": _("Plugin enabled."),
        "disabled": _("Plugin disabled."),
        "locked": _("This plugin is not included in the current plan."),
        "missing": _("Error: Account not found or you don't have permission."),
        "denied": _("Error: Account not found or you don't have permission."),
    }
    await query.answer(messages.get(result, _("Could not change this plugin.")), show_alert=True)
    if result in {"enabled", "disabled"}:
        await show_plugins_menu(update, context, account_id)


async def show_plan_plugins(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    plan_id: int,
    *,
    answer: bool = True,
) -> None:
    query = update.callback_query
    user_id = _user_id(update)
    if user_id is None:
        return
    _ = get_translation_func_for_user(user_id)
    if query is None:
        return
    if answer:
        await query.answer()
    language = get_user_language(user_id)
    views = list_plan_plugin_views(plan_id, language)
    if views is None:
        await query.edit_message_text(_("Error: Plan not found. It might have been deleted."))
        return
    lines = [_("<b>Plan plugins</b>\nChoose which plugins this plan allows.")]
    buttons: list[list[InlineKeyboardButton]] = []
    for view in views:
        if view["allowed"]:
            mark = "✅"
            state = _("Allowed")
        else:
            mark = "🚫"
            state = _("Blocked")
        lines.append(f"\n{mark} <b>{view['name']}</b> — {state}\n{view['description']}")
        buttons.append(
            [
                InlineKeyboardButton(
                    f"{mark} {view['name']}",
                    callback_data=f"admin_planplug_{plan_id}_{view['name']}",
                )
            ]
        )
    buttons.append(
        [
            InlineKeyboardButton(
                _("🔙 Back to Plan List"),
                callback_data=f"admin_plan_edit_{plan_id}",
            )
        ]
    )
    await query.edit_message_text(
        "\n".join(lines),
        reply_markup=InlineKeyboardMarkup(buttons),
        parse_mode=ParseMode.HTML,
    )


async def toggle_plan_plugin(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    plan_id: int,
    plugin_name: str,
) -> None:
    query = update.callback_query
    user_id = _user_id(update)
    if user_id is None:
        return
    _ = get_translation_func_for_user(user_id)
    if query is None:
        return
    language = get_user_language(user_id)
    views = list_plan_plugin_views(plan_id, language) or []
    current = next((view for view in views if view["name"] == plugin_name), None)
    if current is None:
        await query.answer(_("Could not change this plugin."), show_alert=True)
        return
    if not set_plan_plugin_allowed(plan_id, plugin_name, not current["allowed"]):
        await query.answer(_("Could not change this plugin."), show_alert=True)
        return
    await query.answer(_("Plugin access updated."))
    await show_plan_plugins(update, context, plan_id, answer=False)
