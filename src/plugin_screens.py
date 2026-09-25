"""Plain-language screens for an account's features in the control bot.

Buttons use the feature's display name and say what tapping does. Internal
plugin ids stay in callback data only.
"""

from __future__ import annotations

from collections.abc import Callable
from html import escape

from src.command_catalog import CATEGORIES, category_of, plugin_label
from src.help_text import primary_commands
from src.runtime.plugins import command_prefix, get_plugin

Translate = Callable[[str], str]
Rows = list[list[tuple[str, str]]]


def _clip(label: str, limit: int = 60) -> str:
    if len(label) <= limit:
        return label
    return label[: limit - 1] + "…"


def _button(label: str, data: str) -> tuple[str, str]:
    return _clip(label), data


def _state(view: dict, tr: Translate) -> tuple[str, str]:
    if not view.get("allowed"):
        return "🔒", tr("Not available")
    if view.get("enabled"):
        return "🟢", tr("On now")
    return "⚪️", tr("Off now")


def _views(account_id: int, language: str) -> list[dict] | None:
    from src.runtime.gating import list_plugin_views

    found = list_plugin_views(account_id, language)
    if found is None:
        return None
    return list(found)


def build_plugins_home(account_id: int, language: str, tr: Translate) -> tuple[str, Rows] | None:
    views = _views(account_id, language)
    if views is None:
        return None
    on = sum(1 for view in views if view.get("allowed") and view.get("enabled"))
    off = sum(1 for view in views if view.get("allowed") and not view.get("enabled"))
    locked = sum(1 for view in views if not view.get("allowed"))
    text = tr(
        "<b>🧩 Account features</b>\n"
        "Turn features on or off for this account.\n"
        "Tap a section, then tap a feature. You will see what it does before anything changes.\n"
        "🟢 On now · ⚪️ Off · 🔒 Not in your plan\n"
        "On: {on} · Off: {off} · Locked: {locked}"
    ).format(on=on, off=off, locked=locked)
    rows: Rows = []
    pair: list[tuple[str, str]] = []
    for item in CATEGORIES:
        pair.append(
            _button(
                f"{item.emoji} {item.title(language)}",
                f"mng_pcat_{account_id}_{item.id}",
            )
        )
        if len(pair) == 2:
            rows.append(pair)
            pair = []
    if pair:
        rows.append(pair)
    rows.append([_button(tr("🔙 Back to this account"), f"mng_select_{account_id}")])
    return text, rows


def build_plugin_category(
    account_id: int, language: str, category_id: str, tr: Translate
) -> tuple[str, Rows] | None:
    views = _views(account_id, language)
    if views is None:
        return None
    category = next((item for item in CATEGORIES if item.id == category_id), None)
    if category is None:
        return build_plugins_home(account_id, language, tr)
    lines = [
        tr(
            "<b>{emoji} {title}</b>\n"
            "Tap a feature. The button shows its name and whether it is on.\n"
            "The next screen explains it and lets you turn it on or off."
        ).format(emoji=category.emoji, title=escape(category.title(language)))
    ]
    rows: Rows = []
    for view in views:
        if category_of(view["name"]).id != category_id:
            continue
        label = plugin_label(view["name"], language)
        mark, state = _state(view, tr)
        lines.append(
            f"\n{mark} <b>{escape(label)}</b> — {escape(state)}\n{escape(view['description'])}"
        )
        rows.append(
            [
                _button(
                    f"{mark} {label} — {state}",
                    f"mng_pview_{account_id}_{view['name']}",
                )
            ]
        )
    if len(rows) == 0:
        lines.append("\n" + tr("This section has no features."))
    rows.append([_button(tr("↩ Back to features"), f"mng_plugins_{account_id}")])
    rows.append([_button(tr("🔙 Back to this account"), f"mng_select_{account_id}")])
    return "\n".join(lines), rows


def build_plugin_detail(
    account_id: int, language: str, plugin_name: str, tr: Translate
) -> tuple[str, Rows] | None:
    views = _views(account_id, language)
    plugin = get_plugin(plugin_name)
    if views is None or plugin is None:
        return None
    view = next((item for item in views if item["name"] == plugin_name), None)
    if view is None:
        return None
    label = plugin_label(plugin_name, language)
    mark, state = _state(view, tr)
    if not view["allowed"]:
        lead = tr("This feature is not in your plan, so it cannot be turned on here.")
    elif view["enabled"]:
        lead = tr("This feature is on. Use the button below to turn it off.")
    else:
        lead = tr("This feature is off. Use the button below to turn it on.")
    prefix = command_prefix(account_id)
    lines = [
        f"<b>{mark} {escape(label)}</b>",
        escape(lead),
        escape(plugin.meta.description(language)),
        tr("Status: {state}").format(state=escape(f"{mark} {state}")),
        "",
        tr("<b>Commands</b>"),
        tr(
            "Send these from the account, or from your own account if you are "
            "the owner or an admin. They start with {prefix}."
        ).format(prefix=escape(prefix)),
    ]
    for command in primary_commands(plugin):
        names = " · ".join(
            f"{prefix}{item.name}"
            for item in plugin.meta.commands
            if item.description_en == command.description_en
        )
        lines.append(f"📖 {escape(names)} — {escape(command.description(language))}")
    rows: Rows = []
    if view["allowed"]:
        if view["enabled"]:
            rows.append(
                [
                    _button(
                        tr("⏸ Turn off {name}").format(name=label),
                        f"mng_plugtog_{account_id}_{plugin_name}",
                    )
                ]
            )
        else:
            rows.append(
                [
                    _button(
                        tr("▶️ Turn on {name}").format(name=label),
                        f"mng_plugtog_{account_id}_{plugin_name}",
                    )
                ]
            )
    if plugin.meta.settings and view["allowed"]:
        rows.append(
            [
                _button(
                    tr("⚙️ Settings for {name}").format(name=label),
                    f"mng_plugcfg_{account_id}_{plugin_name}",
                )
            ]
        )
    section = category_of(plugin_name)
    rows.append(
        [
            _button(
                tr("↩ Back to {section}").format(section=section.title(language)),
                f"mng_pcat_{account_id}_{section.id}",
            )
        ]
    )
    rows.append([_button(tr("🏠 Features home"), f"mng_plugins_{account_id}")])
    text = "\n".join(lines)
    if len(text) > 3900:
        text = text[:3899] + "…"
    return text, rows


def build_plugin_settings(
    account_id: int, language: str, plugin_name: str, tr: Translate
) -> tuple[str, Rows] | None:
    from src.runtime.settings_form import current_setting

    plugin = get_plugin(plugin_name)
    if plugin is None or not plugin.meta.settings:
        return None
    label = plugin_label(plugin_name, language)
    lines = [
        tr(
            "<b>⚙️ Settings for {name}</b>\n"
            "Tap a button to change that setting.\n"
            "An on/off button switches immediately.\n"
            "Other settings ask you to send the new value.\n"
            "Send - to restore the original value, or الغاء to stop."
        ).format(name=escape(label))
    ]
    rows: Rows = []
    for field in plugin.meta.settings:
        value = current_setting(account_id, plugin.meta.name, field)
        field_label = field.label(language)
        if field.kind == "bool":
            if value:
                shown = tr("On now")
                action = tr("⏸ Turn off: {label}").format(label=field_label)
            else:
                shown = tr("Off now")
                action = tr("▶️ Turn on: {label}").format(label=field_label)
            lines.append(f"\n<b>{escape(field_label)}</b>: {escape(shown)}")
            button = action
        else:
            if value is None or value == "":
                shown = "—"
            else:
                shown = str(value)
                if len(shown) > 40:
                    shown = shown[:37] + "..."
            lines.append(f"\n<b>{escape(field_label)}</b>: <code>{escape(shown)}</code>")
            button = tr("✏️ Change {label} — now: {value}").format(label=field_label, value=shown)
        rows.append(
            [
                _button(
                    button,
                    f"mng_pf_{account_id}_{plugin.meta.name}_{field.key}",
                )
            ]
        )
    rows.append(
        [
            _button(
                tr("↩ Back to {name}").format(name=label), f"mng_pview_{account_id}_{plugin_name}"
            )
        ]
    )
    rows.append([_button(tr("🏠 Features home"), f"mng_plugins_{account_id}")])
    return "\n".join(lines), rows
