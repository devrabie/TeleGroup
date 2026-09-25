"""Control-bot screens for per-account plugins and per-plan allowlists."""

from __future__ import annotations

import logging
from html import escape

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Message, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from src.database import get_user_language, user_owns_account
from src.runtime.gating import list_plan_plugin_views, list_plugin_views, set_account_plugin
from src.runtime.plugins import command_prefix, get_plugin
from src.runtime.settings_form import coerce_setting, current_setting, save_setting
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
        if view["commands"] and sum(len(line) for line in lines) < 2800:
            shown = view["commands"][:6]
            extra = len(view["commands"]) - len(shown)
            line = " ".join(f"<code>{prefix}{name}</code>" for name in shown)
            if extra:
                line += f" +{extra}"
            lines.append(line)
        row = [
            InlineKeyboardButton(
                f"{mark} {view['name']}",
                callback_data=f"mng_plugtog_{account_id}_{view['name']}",
            )
        ]
        plugin = get_plugin(view["name"])
        if plugin is not None and plugin.meta.settings and view["allowed"]:
            row.append(
                InlineKeyboardButton(
                    _("⚙️ Settings"),
                    callback_data=f"mng_plugcfg_{account_id}_{view['name']}",
                )
            )
        buttons.append(row)
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


def _field_value_label(value: object, language: str) -> str:
    if isinstance(value, bool):
        if language == "ar":
            return "تشغيل" if value else "ايقاف"
        return "on" if value else "off"
    if value is None or value == "":
        return "—"
    text = str(value)
    if len(text) > 40:
        return text[:37] + "..."
    return text


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
    lines = [
        _(
            "<b>{name} settings</b>\nBooleans switch when tapped. Other fields ask for a new value."
        ).format(name=plugin.meta.name)
    ]
    buttons: list[list[InlineKeyboardButton]] = []
    for field in plugin.meta.settings:
        value = current_setting(account_id, plugin.meta.name, field)
        label = field.label(language)
        shown = _field_value_label(value, language)
        lines.append(f"\n<b>{escape(label)}</b>: <code>{escape(shown)}</code>")
        buttons.append(
            [
                InlineKeyboardButton(
                    f"{label}: {shown}",
                    callback_data=f"mng_pf_{account_id}_{plugin.meta.name}_{field.key}",
                )
            ]
        )
    buttons.append([InlineKeyboardButton(_("🔙 Back"), callback_data=f"mng_plugins_{account_id}")])
    await query.edit_message_text(
        "\n".join(lines),
        reply_markup=InlineKeyboardMarkup(buttons),
        parse_mode=ParseMode.HTML,
    )


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
