"""Control-bot screens for per-account plugins and per-plan allowlists."""

from __future__ import annotations

import logging
from html import escape

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Message, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from src.command_catalog import plugin_label
from src.database import get_user_language, user_owns_account
from src.plugin_screens import (
    build_plugin_category,
    build_plugin_detail,
    build_plugin_settings,
    build_plugins_home,
)
from src.runtime.gating import list_plan_plugin_views, set_account_plugin
from src.runtime.plugins import get_plugin
from src.runtime.settings_form import coerce_setting, current_setting, save_setting
from src.runtime.store import set_plan_plugin_allowed
from src.translation import get_translation_func_for_user

log = logging.getLogger(__name__)


def _markup(rows: list[list[tuple[str, str]]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(label, callback_data=data) for label, data in row] for row in rows]
    )


async def _show(query: object, text: str, rows: list[list[tuple[str, str]]]) -> None:
    message = getattr(query, "message", None)
    edit = getattr(query, "edit_message_text", None)
    if message is None or edit is None:
        return
    await edit(text, reply_markup=_markup(rows), parse_mode=ParseMode.HTML)


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
    built = build_plugins_home(account_id, language, _)
    if built is None:
        await query.answer(
            _("Error: Account not found or you don't have permission."), show_alert=True
        )
        return
    text, rows = built
    await _show(query, text, rows)


async def show_plugin_category(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    account_id: int,
    category_id: str,
) -> None:
    del context
    query = update.callback_query
    user_id = _user_id(update)
    if user_id is None or query is None:
        return
    _ = get_translation_func_for_user(user_id)
    if not user_owns_account(account_id, user_id):
        await query.answer(
            _("Error: Account not found or you don't have permission."), show_alert=True
        )
        return
    language = get_user_language(user_id)
    built = build_plugin_category(account_id, language, category_id, _)
    if built is None:
        await query.answer(
            _("Error: Account not found or you don't have permission."), show_alert=True
        )
        return
    text, rows = built
    await _show(query, text, rows)


async def show_plugin_detail(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    account_id: int,
    plugin_name: str,
) -> None:
    query = update.callback_query
    user_id = _user_id(update)
    if user_id is None or query is None:
        return
    _ = get_translation_func_for_user(user_id)
    if not user_owns_account(account_id, user_id):
        await query.answer(
            _("Error: Account not found or you don't have permission."), show_alert=True
        )
        return
    language = get_user_language(user_id)
    built = build_plugin_detail(account_id, language, plugin_name, _)
    if built is None:
        await show_plugins_menu(update, context, account_id)
        return
    text, rows = built
    await _show(query, text, rows)


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
        await show_plugin_detail(update, context, account_id, plugin_name)


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
    lines = [
        _(
            "<b>Plan features</b>\n"
            "Tap a feature to allow it or block it on this plan. "
            "The name is what people see, not an internal id."
        )
    ]
    buttons: list[list[InlineKeyboardButton]] = []
    for view in views:
        label = plugin_label(view["name"], language)
        if view["allowed"]:
            mark = "✅"
            state = _("Allowed")
        else:
            mark = "🚫"
            state = _("Blocked")
        lines.append(f"\n{mark} <b>{escape(label)}</b> — {state}\n{escape(view['description'])}")
        buttons.append(
            [
                InlineKeyboardButton(
                    f"{mark} {label} — {state}",
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


async def show_plugin_settings(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    account_id: int,
    plugin_name: str,
) -> None:
    query = update.callback_query
    user_id = _user_id(update)
    if user_id is None or query is None or query.message is None:
        return
    _ = get_translation_func_for_user(user_id)
    if not user_owns_account(account_id, user_id):
        await query.answer(
            _("Error: Account not found or you don't have permission."), show_alert=True
        )
        return
    plugin = get_plugin(plugin_name)
    if plugin is None or not plugin.meta.settings:
        await query.answer(_("This plugin has no settings."), show_alert=True)
        return
    language = get_user_language(user_id)
    built = build_plugin_settings(account_id, language, plugin_name, _)
    if built is None:
        await query.answer(_("This plugin has no settings."), show_alert=True)
        return
    text, rows = built
    await _show(query, text, rows)


async def edit_plugin_setting(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    account_id: int,
    plugin_name: str,
    field_key: str,
) -> None:
    query = update.callback_query
    user_id = _user_id(update)
    if user_id is None or query is None:
        return
    _ = get_translation_func_for_user(user_id)
    if not user_owns_account(account_id, user_id):
        await query.answer(
            _("Error: Account not found or you don't have permission."), show_alert=True
        )
        return
    plugin = get_plugin(plugin_name)
    field = None
    if plugin is not None:
        field = next((item for item in plugin.meta.settings if item.key == field_key), None)
    if plugin is None or field is None:
        await query.answer(_("This plugin has no settings."), show_alert=True)
        return
    if field.kind == "bool":
        current = bool(current_setting(account_id, plugin.meta.name, field))
        save_setting(account_id, plugin.meta.name, field, not current)
        await query.answer(_("Setting saved."))
        await show_plugin_settings(update, context, account_id, plugin_name)
        return
    language = get_user_language(user_id)
    user_data = context.user_data
    prompt = query.message
    if user_data is None or not isinstance(prompt, Message):
        await query.answer(_("This plugin has no settings."), show_alert=True)
        return
    user_data["plugin_setting_field"] = {
        "account_id": account_id,
        "plugin_name": plugin.meta.name,
        "field_key": field.key,
    }
    await query.answer()
    await prompt.reply_text(
        _("Send the new value for <b>{label}</b>. Send - to reset, or cancel to stop.").format(
            label=field.label(language)
        ),
        parse_mode=ParseMode.HTML,
    )


async def receive_plugin_setting(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_data = context.user_data
    pending = user_data.get("plugin_setting_field") if user_data else None
    if not pending or user_data is None or update.message is None or update.effective_user is None:
        return
    user_id = update.effective_user.id
    _ = get_translation_func_for_user(user_id)
    text = update.message.text or ""
    if text.strip().casefold() in {"cancel", "الغاء", "إلغاء"}:
        user_data.pop("plugin_setting_field", None)
        await update.message.reply_text(_("Cancelled."))
        return
    if not user_owns_account(int(pending["account_id"]), user_id):
        user_data.pop("plugin_setting_field", None)
        await update.message.reply_text(_("Error: Account not found or you don't have permission."))
        return
    plugin = get_plugin(str(pending["plugin_name"]))
    field = None
    if plugin is not None:
        field = next(
            (item for item in plugin.meta.settings if item.key == pending["field_key"]),
            None,
        )
    if plugin is None or field is None:
        user_data.pop("plugin_setting_field", None)
        await update.message.reply_text(_("This plugin has no settings."))
        return
    ok, value = coerce_setting(field, text)
    if not ok:
        await update.message.reply_text(_("That value is not valid."))
        return
    save_setting(int(pending["account_id"]), plugin.meta.name, field, value)
    user_data.pop("plugin_setting_field", None)
    await update.message.reply_text(_("Setting saved."))
