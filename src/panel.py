"""Signed inline control panel: screens, authorization, and toggles.

Callback data stays under Telegram's 64-byte limit. The signature uses the
session encryption key. A press is accepted only from the managed account's
own Telegram user or from the registered owner.
"""

from __future__ import annotations

import hashlib
import hmac
import re
from dataclasses import dataclass
from html import escape

from src.command_catalog import CATEGORIES, category_of, plugin_label
from src.config import get_settings
from src.templates import apply_prefix, field, is_ar, panel_closed, panel_home, pick

_OPS = frozenset({"h", "c", "g", "d", "t", "s", "k", "x"})
_ARG = re.compile(r"^[a-z0-9:+-]*$")
_ALPHABET = "0123456789abcdefghijklmnopqrstuvwxyz"


@dataclass(frozen=True)
class PanelCallback:
    account_id: int
    account_user_id: int
    op: str
    arg: str


def _sign(body: str) -> str:
    key = get_settings().session_encryption_key.encode()
    digest = hmac.new(key, body.encode(), hashlib.sha256).hexdigest()
    return digest[:8]


def _b36(number: int) -> str:
    if number < 0:
        raise ValueError("negative id")
    if number == 0:
        return "0"
    chars: list[str] = []
    value = number
    while value:
        value, rest = divmod(value, 36)
        chars.append(_ALPHABET[rest])
    return "".join(reversed(chars))


def _b36_dec(text: str) -> int | None:
    if not text or any(char not in _ALPHABET for char in text):
        return None
    return int(text, 36)


def pack_query(account_id: int, account_user_id: int) -> str:
    sig = _sign(f"q:{account_id}:{account_user_id}")
    return f"tg.{_b36(account_id)}.{_b36(account_user_id)}.{sig}"


def unpack_query(text: str) -> tuple[int, int] | None:
    parts = (text or "").strip().split(".")
    if len(parts) != 4 or parts[0] != "tg":
        return None
    account_id = _b36_dec(parts[1])
    account_user_id = _b36_dec(parts[2])
    if account_id is None or account_user_id is None:
        return None
    expected = _sign(f"q:{account_id}:{account_user_id}")
    if not hmac.compare_digest(expected, parts[3]):
        return None
    return account_id, account_user_id


def pack_callback(account_id: int, account_user_id: int, op: str, arg: str = "") -> str:
    if op not in _OPS or not _ARG.fullmatch(arg):
        raise ValueError("bad callback")
    data = (
        f"p.{_b36(account_id)}.{_b36(account_user_id)}.{op}.{arg}."
        f"{_sign(f'{account_id}:{account_user_id}:{op}:{arg}')}"
    )
    if len(data.encode("utf-8")) > 64:
        raise ValueError("callback_data longer than 64 bytes")
    return data


def unpack_callback(data: str) -> PanelCallback | None:
    parts = (data or "").split(".")
    if len(parts) != 6 or parts[0] != "p":
        return None
    account_id = _b36_dec(parts[1])
    account_user_id = _b36_dec(parts[2])
    op = parts[3]
    arg = parts[4]
    sig = parts[5]
    if account_id is None or account_user_id is None or op not in _OPS:
        return None
    if not _ARG.fullmatch(arg) or len(sig) != 8:
        return None
    expected = _sign(f"{account_id}:{account_user_id}:{op}:{arg}")
    if not hmac.compare_digest(expected, sig):
        return None
    return PanelCallback(account_id, account_user_id, op, arg)


def actor_allowed(account_id: int, actor_id: int, account_user_id: int) -> bool:
    if int(actor_id) == int(account_user_id):
        return True
    from src.runtime.store import get_plugin_account

    account = get_plugin_account(account_id)
    if account is None or account.get("deleted"):
        return False
    owner = account.get("telegram_id")
    return owner is not None and int(owner) == int(actor_id)


def toggle_plugin(account_id: int, actor_id: int, account_user_id: int, plugin_name: str) -> str:
    if not actor_allowed(account_id, actor_id, account_user_id):
        return "denied"
    from src.runtime.gating import set_account_plugin
    from src.runtime.store import get_plugin_account

    account = get_plugin_account(account_id)
    if account is None or account.get("deleted"):
        return "missing"
    return set_account_plugin(account_id, int(account["telegram_id"]), plugin_name)


def adjust_setting(
    account_id: int,
    actor_id: int,
    account_user_id: int,
    plugin_name: str,
    field_index: int,
    action: str,
) -> str:
    if not actor_allowed(account_id, actor_id, account_user_id):
        return "denied"
    from src.runtime.plugins import get_plugin
    from src.runtime.settings_form import current_setting, save_setting

    plugin = get_plugin(plugin_name)
    if plugin is None or field_index < 0 or field_index >= len(plugin.meta.settings):
        return "missing"
    field_spec = plugin.meta.settings[field_index]
    current = current_setting(account_id, plugin_name, field_spec)
    if field_spec.kind == "str":
        return "readonly"
    if action == "t" and field_spec.kind == "bool":
        save_setting(account_id, plugin_name, field_spec, not bool(current))
        return "saved"
    if field_spec.kind == "int" and action in {"p", "m"}:
        try:
            number = int(current if current is not None else field_spec.default or 0)
        except (TypeError, ValueError):
            number = int(field_spec.default or 0)
        span = (field_spec.maximum or number) - (field_spec.minimum or number)
        step = 1 if span <= 20 else 5
        number += step if action == "p" else -step
        if field_spec.minimum is not None:
            number = max(field_spec.minimum, number)
        if field_spec.maximum is not None:
            number = min(field_spec.maximum, number)
        save_setting(account_id, plugin_name, field_spec, number)
        return "saved"
    return "readonly"


ButtonRows = list[list[tuple[str, str]]]


def _rows_of(pairs: list[tuple[str, str]], width: int) -> ButtonRows:
    rows: ButtonRows = []
    row: list[tuple[str, str]] = []
    for pair in pairs:
        row.append(pair)
        if len(row) == width:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return rows


def _button(
    account_id: int, account_user_id: int, label: str, op: str, arg: str = ""
) -> tuple[str, str]:
    return label[:60], pack_callback(account_id, account_user_id, op, arg)


def _nav(
    account_id: int,
    account_user_id: int,
    language: str,
    *,
    back: tuple[str, tuple[str, str]] | None = None,
) -> ButtonRows:
    rows: ButtonRows = []
    if back is not None:
        rows.append([_button(account_id, account_user_id, back[0], back[1][0], back[1][1])])
    home = "🏠 الرئيسية" if is_ar(language) else "🏠 Home"
    close = "✖ إغلاق" if is_ar(language) else "✖ Close"
    rows.append(
        [
            _button(account_id, account_user_id, home, "h"),
            _button(account_id, account_user_id, close, "x"),
        ]
    )
    return rows


def _language(account_id: int) -> str:
    from src.runtime.store import get_plugin_account

    account = get_plugin_account(account_id)
    if account is None:
        return "ar"
    code = str(account.get("language_code") or "ar")
    return "ar" if code == "ar" else "en"


def _prefix(account_id: int) -> str:
    from src.runtime.plugins import command_prefix

    return command_prefix(account_id)


def _views(account_id: int, language: str) -> list[dict]:
    from src.runtime.gating import list_plugin_views

    return list(list_plugin_views(account_id, language) or [])


def _plugins() -> dict:
    from src.runtime.plugins import all_plugins

    return {plugin.meta.name: plugin for plugin in all_plugins()}


def render(
    account_id: int, account_user_id: int, op: str = "h", arg: str = ""
) -> tuple[str, ButtonRows]:
    language = _language(account_id)
    if op == "x":
        return panel_closed(language), []
    if op == "c" and arg:
        return _category(account_id, account_user_id, language, arg)
    if op == "g" and arg:
        return _plugin(account_id, account_user_id, language, arg)
    if op == "d" and ":" in arg:
        return _command(account_id, account_user_id, language, arg)
    if op == "s" and arg:
        return _settings(account_id, account_user_id, language, arg)
    return _home(account_id, account_user_id, language)


def _home(account_id: int, account_user_id: int, language: str) -> tuple[str, ButtonRows]:
    prefix = escape(_prefix(account_id))
    pairs = [
        _button(account_id, account_user_id, item.label(language), "c", item.id)
        for item in CATEGORIES
    ]
    return panel_home(language, prefix), _rows_of(pairs, 2) + _nav(
        account_id, account_user_id, language
    )


def _category(
    account_id: int, account_user_id: int, language: str, category_id: str
) -> tuple[str, ButtonRows]:
    from src.command_catalog import CATEGORY_BY_ID

    category = CATEGORY_BY_ID.get(category_id)
    if category is None:
        return _home(account_id, account_user_id, language)
    plugins = _plugins()
    lines = []
    pairs: list[tuple[str, str]] = []
    for view in _views(account_id, language):
        if category_of(view["name"]).id != category_id:
            continue
        plugin = plugins.get(view["name"])
        label = plugin_label(view["name"], language)
        if not view["allowed"]:
            mark = "🔒"
            state = "غير مشمولة" if is_ar(language) else "Locked"
        elif view["enabled"]:
            mark = "🟢"
            state = "تعمل" if is_ar(language) else "On"
        else:
            mark = "⚪️"
            state = "متوقفة" if is_ar(language) else "Off"
        description = escape(str(view["description"]))
        lines.append(f"{mark} <b>{escape(label)}</b> — {escape(state)}\n{description}")
        if plugin is not None and plugin.meta.commands:
            shown = plugin.meta.commands[:4]
            extra = len(plugin.meta.commands) - len(shown)
            names = " ".join(escape(f"{_prefix(account_id)}{item.name}") for item in shown)
            if extra:
                names += f" +{extra}"
            lines.append(names)
        pairs.append(_button(account_id, account_user_id, f"{mark} {label}", "g", view["name"]))
    if not lines:
        lines.append(
            "لا توجد إضافات في هذا القسم." if is_ar(language) else "This section is empty."
        )
    title = escape(category.label(language))
    text = f"<b>{title}</b>\n⋆┄─┄─┄─┄┄─┄─┄─┄─┄┄⋆\n" + "\n".join(lines)
    back = ("↩ رجوع" if is_ar(language) else "↩ Back", ("h", ""))
    return text, _rows_of(pairs, 1) + _nav(account_id, account_user_id, language, back=back)


def _plugin(
    account_id: int, account_user_id: int, language: str, plugin_name: str
) -> tuple[str, ButtonRows]:
    plugins = _plugins()
    plugin = plugins.get(plugin_name)
    view = next(
        (item for item in _views(account_id, language) if item["name"] == plugin_name),
        None,
    )
    if plugin is None or view is None:
        return _home(account_id, account_user_id, language)
    label = plugin_label(plugin_name, language)
    prefix = _prefix(account_id)
    if not view["allowed"]:
        state = "🔒 " + ("غير مشمولة في الخطة" if is_ar(language) else "Not in your plan")
    elif view["enabled"]:
        state = "🟢 " + ("تعمل" if is_ar(language) else "On")
    else:
        state = "⚪️ " + ("متوقفة" if is_ar(language) else "Off")
    lines = [
        f"<b>{escape(label)}</b>",
        "⋆┄─┄─┄─┄┄─┄─┄─┄─┄┄⋆",
        escape(plugin.meta.description(language)),
        escape(field("الحالة" if is_ar(language) else "Status", state)),
    ]
    pairs: list[tuple[str, str]] = []
    seen: set[str] = set()
    for index, command in enumerate(plugin.meta.commands):
        if command.description_en in seen:
            continue
        if (
            any(
                (not item.name.isascii()) and item.description_en == command.description_en
                for item in plugin.meta.commands
            )
            and command.name.isascii()
        ):
            continue
        seen.add(command.description_en)
        lines.append(
            escape(
                field(
                    f"{prefix}{command.name}",
                    command.description(language),
                )
            )
        )
        pairs.append(
            _button(
                account_id,
                account_user_id,
                command.name,
                "d",
                f"{plugin_name}:{index}",
            )
        )
    buttons = _rows_of(pairs, 2)
    if view["allowed"]:
        if view["enabled"]:
            toggle_label = "⏸ إيقاف" if is_ar(language) else "⏸ Disable"
        else:
            toggle_label = "▶️ تشغيل" if is_ar(language) else "▶️ Enable"
        buttons.append([_button(account_id, account_user_id, toggle_label, "t", plugin_name)])
    if plugin.meta.settings and view["allowed"]:
        settings_label = "⚙️ الإعدادات" if is_ar(language) else "⚙️ Settings"
        buttons.append([_button(account_id, account_user_id, settings_label, "s", plugin_name)])
    back_label = "↩ القسم" if is_ar(language) else "↩ Section"
    back = (back_label, ("c", category_of(plugin_name).id))
    return "\n".join(lines), buttons + _nav(account_id, account_user_id, language, back=back)


def _command(
    account_id: int, account_user_id: int, language: str, arg: str
) -> tuple[str, ButtonRows]:
    plugin_name, _, raw_index = arg.partition(":")
    plugins = _plugins()
    plugin = plugins.get(plugin_name)
    if plugin is None or not raw_index.isdigit():
        return _home(account_id, account_user_id, language)
    index = int(raw_index)
    if index < 0 or index >= len(plugin.meta.commands):
        return _plugin(account_id, account_user_id, language, plugin_name)
    command = plugin.meta.commands[index]
    prefix = _prefix(account_id)
    names = " · ".join(
        escape(f"{prefix}{item.name}")
        for item in plugin.meta.commands
        if item.description_en == command.description_en
    )
    usage = escape(apply_prefix(command.usage(language), prefix))
    example = escape(apply_prefix(command.example(language), prefix))
    if is_ar(language):
        body = "\n".join(
            [
                escape(field("الوصف", command.description(language))),
                f"● الاستخدام: {usage}",
                f"● مثال: {example}",
                f"● الأسماء: {names}",
            ]
        )
    else:
        body = "\n".join(
            [
                escape(field("Description", command.description(language))),
                f"● Usage: {usage}",
                f"● Example: {example}",
                f"● Names: {names}",
            ]
        )
    text = f"<b>{escape(prefix + command.name)}</b>\n⋆┄─┄─┄─┄┄─┄─┄─┄─┄┄⋆\n{body}"
    back = ("↩ الإضافة" if is_ar(language) else "↩ Plugin", ("g", plugin_name))
    return text, _nav(account_id, account_user_id, language, back=back)


def _settings(
    account_id: int, account_user_id: int, language: str, plugin_name: str
) -> tuple[str, ButtonRows]:
    from src.runtime.settings_form import current_setting

    plugins = _plugins()
    plugin = plugins.get(plugin_name)
    if plugin is None or not plugin.meta.settings:
        return _plugin(account_id, account_user_id, language, plugin_name)
    lines = [
        f"<b>{escape(plugin_label(plugin_name, language))}</b>",
        "⋆┄─┄─┄─┄┄─┄─┄─┄─┄┄⋆",
        "الإعدادات" if is_ar(language) else "Settings",
    ]
    buttons: ButtonRows = []
    for index, spec in enumerate(plugin.meta.settings):
        current = current_setting(account_id, plugin_name, spec)
        label = spec.label(language)
        if spec.kind == "bool":
            mark = "🟢" if current else "⚪️"
            state = (
                ("تشغيل" if current else "إيقاف")
                if is_ar(language)
                else ("On" if current else "Off")
            )
            lines.append(escape(field(label, f"{mark} {state}")))
            buttons.append(
                [
                    _button(
                        account_id,
                        account_user_id,
                        f"{mark} {label}",
                        "k",
                        f"{plugin_name}:{index}:t",
                    )
                ]
            )
        elif spec.kind == "int":
            lines.append(escape(field(label, current)))
            buttons.append(
                [
                    _button(account_id, account_user_id, "−", "k", f"{plugin_name}:{index}:m"),
                    _button(
                        account_id,
                        account_user_id,
                        str(current),
                        "s",
                        plugin_name,
                    ),
                    _button(account_id, account_user_id, "+", "k", f"{plugin_name}:{index}:p"),
                ]
            )
        else:
            shown = str(current if current is not None else "")
            if len(shown) > 80:
                shown = shown[:77] + "…"
            lines.append(escape(field(label, shown or "—")))
            note = (
                "النص يُعدّل من أمر الإضافة."
                if is_ar(language)
                else "Change this text with the plugin command."
            )
            lines.append(escape(note))
    back = ("↩ الإضافة" if is_ar(language) else "↩ Plugin", ("g", plugin_name))
    return "\n".join(lines), buttons + _nav(account_id, account_user_id, language, back=back)


def alert_text(language: str, code: str) -> str:
    table = {
        "enabled": ("Plugin enabled.", "تم تشغيل الإضافة."),
        "disabled": ("Plugin disabled.", "تم إيقاف الإضافة."),
        "locked": ("This plugin is not included in your plan.", "هذه الإضافة غير مشمولة في خطتك."),
        "denied": ("Only the account owner can use this button.", "هذا الزر لصاحب الحساب فقط."),
        "missing": ("That plugin was not found.", "تعذر العثور على الإضافة."),
        "saved": ("Saved. It applies without a restart.", "تم الحفظ. يسري بدون إعادة تشغيل."),
        "readonly": (
            "Change this text with the plugin command.",
            "عدّل هذا النص من أمر الإضافة.",
        ),
    }
    en, ar = table.get(code, ("Could not update that.", "تعذر التعديل."))
    return pick(language, en, ar)
