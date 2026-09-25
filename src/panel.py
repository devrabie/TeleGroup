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

_OPS = frozenset({"h", "c", "g", "d", "t", "s", "k", "x", "a", "r"})
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
    if op == "a":
        if arg == "add":
            return _admins_add(account_id, account_user_id, language)
        return _admins(account_id, account_user_id, language)
    return _home(account_id, account_user_id, language)


def _home(account_id: int, account_user_id: int, language: str) -> tuple[str, ButtonRows]:
    prefix = escape(_prefix(account_id))
    pairs = [
        _button(
            account_id,
            account_user_id,
            f"{item.emoji} {item.title(language)}",
            "c",
            item.id,
        )
        for item in CATEGORIES
    ]
    admins = "👥 المسؤولون" if is_ar(language) else "👥 Admins"
    rows = _rows_of(pairs, 2)
    rows.append([_button(account_id, account_user_id, admins, "a")])
    return panel_home(language, prefix), rows + _nav(account_id, account_user_id, language)


def _view_state(language: str, view: dict) -> tuple[str, str]:
    if not view.get("allowed"):
        if is_ar(language):
            return "🔒", "غير متاحة"
        return "🔒", "Not in plan"
    if view.get("enabled"):
        if is_ar(language):
            return "🟢", "تعمل الآن"
        return "🟢", "On"
    if is_ar(language):
        return "⚪️", "متوقفة"
    return "⚪️", "Off"


def _short(text: str, limit: int = 26) -> str:
    cleaned = " ".join(text.split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 1] + "…"


def _category(
    account_id: int, account_user_id: int, language: str, category_id: str
) -> tuple[str, ButtonRows]:
    from src.command_catalog import CATEGORY_BY_ID

    category = CATEGORY_BY_ID.get(category_id)
    if category is None:
        return _home(account_id, account_user_id, language)
    lines = []
    pairs: list[tuple[str, str]] = []
    for view in _views(account_id, language):
        if category_of(view["name"]).id != category_id:
            continue
        label = plugin_label(view["name"], language)
        mark, state = _view_state(language, view)
        description = escape(str(view["description"]))
        lines.append(f"{mark} <b>{escape(label)}</b> — {escape(state)}\n{description}")
        pairs.append(
            _button(account_id, account_user_id, f"{mark} {label} — {state}", "g", view["name"])
        )
    if not lines:
        lines.append(
            "لا توجد ميزات في هذا القسم." if is_ar(language) else "This section has no features."
        )
    title = escape(f"{category.emoji} {category.title(language)}")
    if is_ar(language):
        intro = (
            "اضغط اسم الميزة لفتحها.\n"
            "من الشاشة التالية تشغّلها أو توقفها وتقرأ أوامرها.\n"
            "🟢 تعمل الآن · ⚪️ متوقفة · 🔒 غير متاحة في خطتك"
        )
    else:
        intro = (
            "Tap a feature to open it.\n"
            "The next screen turns it on or off and shows its commands.\n"
            "🟢 On · ⚪️ Off · 🔒 Not in your plan"
        )
    text = f"<b>{title}</b>\n⋆┄─┄─┄─┄┄─┄─┄─┄─┄┄⋆\n{intro}\n\n" + "\n".join(lines)
    back_label = "↩ رجوع للأقسام" if is_ar(language) else "↩ Back to sections"
    back = (back_label, ("h", ""))
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
    mark, state = _view_state(language, view)
    if not view["allowed"]:
        lead = (
            "هذه الميزة غير متاحة في خطتك، لذلك لا يمكن تشغيلها من هنا."
            if is_ar(language)
            else "This feature is not in your plan, so it cannot be turned on here."
        )
    elif view["enabled"]:
        lead = (
            "هذه الميزة تعمل الآن. اضغط الزر لإيقافها، أو اضغط أمراً لترى مثالاً."
            if is_ar(language)
            else (
                "This feature is on. Tap the button to turn it off, "
                "or tap a command for an example."
            )
        )
    else:
        lead = (
            "هذه الميزة متوقفة. اضغط الزر لتشغيلها، أو اضغط أمراً لتعرف ماذا تفعل."
            if is_ar(language)
            else (
                "This feature is off. Tap the button to turn it on, "
                "or tap a command to see what it does."
            )
        )
    lines = [
        f"<b>{mark} {escape(label)}</b>",
        "⋆┄─┄─┄─┄┄─┄─┄─┄─┄┄⋆",
        escape(lead),
        escape(plugin.meta.description(language)),
        escape(field("الحالة" if is_ar(language) else "Status", f"{mark} {state}")),
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
                f"📖 {command.name}: {_short(command.description(language))}",
                "d",
                f"{plugin_name}:{index}",
            )
        )
    buttons = _rows_of(pairs, 1)
    if view["allowed"]:
        if view["enabled"]:
            toggle_label = f"⏸ إيقاف {label}" if is_ar(language) else f"⏸ Turn off {label}"
        else:
            toggle_label = f"▶️ تشغيل {label}" if is_ar(language) else f"▶️ Turn on {label}"
        buttons.append([_button(account_id, account_user_id, toggle_label, "t", plugin_name)])
    if plugin.meta.settings and view["allowed"]:
        settings_label = f"⚙️ إعدادات {label}" if is_ar(language) else f"⚙️ Settings for {label}"
        buttons.append([_button(account_id, account_user_id, settings_label, "s", plugin_name)])
    section = category_of(plugin_name).title(language)
    back_label = f"↩ رجوع إلى {section}" if is_ar(language) else f"↩ Back to {section}"
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
    from src.runtime.actors import access_label

    usage = escape(apply_prefix(command.usage(language), prefix))
    example = escape(apply_prefix(command.example(language), prefix))
    access = escape(access_label(language, command.name))
    feature = plugin_label(plugin_name, language)
    if is_ar(language):
        lead = "هذا مثال جاهز. أرسله كما هو، أو غيّر الاسم والمعرّف."
        body = "\n".join(
            [
                escape(lead),
                escape(field("الوصف", command.description(language))),
                f"● الاستخدام: {usage}",
                f"● مثال: {example}",
                f"● الأسماء: {names}",
                f"● من يستخدمه: {access}",
            ]
        )
    else:
        lead = "This is a ready example. Send it as written, or change the name and id."
        body = "\n".join(
            [
                escape(lead),
                escape(field("Description", command.description(language))),
                f"● Usage: {usage}",
                f"● Example: {example}",
                f"● Names: {names}",
                f"● Who can use it: {access}",
            ]
        )
    text = f"<b>{escape(prefix + command.name)}</b>\n⋆┄─┄─┄─┄┄─┄─┄─┄─┄┄⋆\n{body}"
    back_label = f"↩ رجوع إلى {feature}" if is_ar(language) else f"↩ Back to {feature}"
    back = (back_label, ("g", plugin_name))
    return text, _nav(account_id, account_user_id, language, back=back)


def _settings(
    account_id: int, account_user_id: int, language: str, plugin_name: str
) -> tuple[str, ButtonRows]:
    from src.runtime.settings_form import current_setting

    plugins = _plugins()
    plugin = plugins.get(plugin_name)
    if plugin is None or not plugin.meta.settings:
        return _plugin(account_id, account_user_id, language, plugin_name)
    feature = plugin_label(plugin_name, language)
    if is_ar(language):
        intro = (
            "اضغط الزر لتغيير الإعداد. التغيير يسري مباشرة.\n"
            "زر التشغيل يبدّل بين يعمل ومتوقف.\n"
            "زر الزيادة والنقصان يغيّر الرقم.\n"
            "النص الطويل يُعدّل من أمر الميزة نفسه."
        )
    else:
        intro = (
            "Tap a button to change that setting. It applies immediately.\n"
            "The on/off button switches the setting.\n"
            "The plus and minus buttons change a number.\n"
            "Long text is changed with the feature's own command."
        )
    lines = [
        f"<b>⚙️ {escape(feature)}</b>",
        "⋆┄─┄─┄─┄┄─┄─┄─┄─┄┄⋆",
        escape(intro),
    ]
    buttons: ButtonRows = []
    for index, spec in enumerate(plugin.meta.settings):
        current = current_setting(account_id, plugin_name, spec)
        label = spec.label(language)
        if spec.kind == "bool":
            if current:
                action = f"⏸ إيقاف {label}" if is_ar(language) else f"⏸ Turn off {label}"
                state = "🟢 تعمل" if is_ar(language) else "🟢 On"
            else:
                action = f"▶️ تشغيل {label}" if is_ar(language) else f"▶️ Turn on {label}"
                state = "⚪️ متوقفة" if is_ar(language) else "⚪️ Off"
            lines.append(escape(field(label, state)))
            buttons.append(
                [_button(account_id, account_user_id, action, "k", f"{plugin_name}:{index}:t")]
            )
        elif spec.kind == "int":
            lines.append(escape(field(label, current)))
            decrease = f"➖ تقليل {label}" if is_ar(language) else f"➖ Decrease {label}"
            increase = f"➕ زيادة {label}" if is_ar(language) else f"➕ Increase {label}"
            buttons.append(
                [_button(account_id, account_user_id, decrease, "k", f"{plugin_name}:{index}:m")]
            )
            buttons.append(
                [_button(account_id, account_user_id, increase, "k", f"{plugin_name}:{index}:p")]
            )
        else:
            shown = str(current if current is not None else "")
            if len(shown) > 80:
                shown = shown[:77] + "…"
            lines.append(escape(field(label, shown or "—")))
            note = (
                "هذا النص يُعدّل من أمر الميزة، وليس من زر."
                if is_ar(language)
                else "Change this text with the feature's command, not a button."
            )
            lines.append(escape(note))
    back_label = f"↩ رجوع إلى {feature}" if is_ar(language) else f"↩ Back to {feature}"
    back = (back_label, ("g", plugin_name))
    return "\n".join(lines), buttons + _nav(account_id, account_user_id, language, back=back)


def _admin_name(row: dict) -> str:
    username = row.get("username")
    if username:
        return f"@{username}"
    return str(row.get("telegram_id"))


def _admins(account_id: int, account_user_id: int, language: str) -> tuple[str, ButtonRows]:
    from src.runtime.store import list_account_admins

    rows = list_account_admins(account_id)
    prefix = escape(_prefix(account_id))
    if is_ar(language):
        intro = (
            "هؤلاء يستطيعون إرسال أوامر الحساب من حساباتهم، في الخاص أو في مجموعة أنتم فيها معاً.\n"
            "أنت والحساب نفسه تستطيعان دائماً. المسؤول لا يضيف مسؤولاً آخر.\n"
            f"للإضافة أرسل {prefix}رفع ادمن بالرد أو المعرّف أو @username، "
            "أو من بوت التحكم في شاشة مسؤولو الأوامر."
        )
        empty = "لا يوجد مسؤولون بعد."
        title = "👥 المسؤولون"
        add_label = "➕ كيف أضيف مسؤولاً"
    else:
        intro = (
            "These people can send this account's commands from their own accounts, "
            "in private or in a group you share.\n"
            "You and the account itself always can. An admin cannot add another admin.\n"
            f"To add one, send {prefix}addadmin by reply, id, or @username, "
            "or use the control bot's admins screen."
        )
        empty = "No admins yet."
        title = "👥 Admins"
        add_label = "➕ How to add an admin"
    lines = [f"<b>{title}</b>", "⋆┄─┄─┄─┄┄─┄─┄─┄─┄┄⋆", escape(intro)]
    if not rows:
        lines.append(escape(empty))
    else:
        for row in rows:
            lines.append(escape(field(str(row["telegram_id"]), _admin_name(row))))
    buttons: ButtonRows = [[_button(account_id, account_user_id, add_label, "a", "add")]]
    for row in rows:
        name = _admin_name(row)
        remove = f"🗑 إزالة {name}" if is_ar(language) else f"🗑 Remove {name}"
        buttons.append(
            [
                _button(
                    account_id,
                    account_user_id,
                    remove,
                    "r",
                    _b36(int(row["telegram_id"])),
                )
            ]
        )
    back = ("↩ رجوع للأقسام" if is_ar(language) else "↩ Back to sections", ("h", ""))
    return "\n".join(lines), buttons + _nav(account_id, account_user_id, language, back=back)


def _admins_add(account_id: int, account_user_id: int, language: str) -> tuple[str, ButtonRows]:
    prefix = escape(_prefix(account_id))
    if is_ar(language):
        body = (
            "الإضافة تتم برسالة، لأن هذا الزر لا يستقبل أسماء.\n"
            f"من الحساب، أو من حسابك في الخاص أو في مجموعة مع الحساب:\n"
            f"● {prefix}رفع ادمن بالرد على رسالته\n"
            f"● {prefix}رفع ادمن 123456789\n"
            f"● {prefix}رفع ادمن @username\n"
            "أو افتح بوت التحكم، ثم الحساب، ثم «مسؤولو الأوامر»، ثم «إضافة مسؤول».\n"
            "فقط صاحب الحساب يستطيع الإضافة والإزالة."
        )
        title = "➕ إضافة مسؤول"
        back_label = "↩ رجوع للمسؤولين"
    else:
        body = (
            "Adding someone needs a message, because this button cannot take a name.\n"
            "From the account, or from your account in private or in a shared group:\n"
            f"● {prefix}addadmin by reply\n"
            f"● {prefix}addadmin 123456789\n"
            f"● {prefix}addadmin @username\n"
            "Or open the control bot, the account, Command admins, then Add an admin.\n"
            "Only the account owner can add or remove admins."
        )
        title = "➕ Add an admin"
        back_label = "↩ Back to admins"
    text = f"<b>{title}</b>\n⋆┄─┄─┄─┄┄─┄─┄─┄─┄┄⋆\n{escape(body)}"
    back = (back_label, ("a", ""))
    return text, _nav(account_id, account_user_id, language, back=back)


def remove_panel_admin(account_id: int, actor_id: int, account_user_id: int, arg: str) -> str:
    if not actor_allowed(account_id, actor_id, account_user_id):
        return "denied"
    admin_id = _b36_dec(arg)
    if admin_id is None:
        return "admin-missing"
    from src.runtime.store import remove_account_admin

    if remove_account_admin(account_id, admin_id):
        return "admin-removed"
    return "admin-missing"


def alert_text(language: str, code: str) -> str:
    table = {
        "enabled": ("Plugin enabled.", "تم تشغيل الإضافة."),
        "disabled": ("Plugin disabled.", "تم إيقاف الإضافة."),
        "locked": ("This plugin is not included in your plan.", "هذه الإضافة غير مشمولة في خطتك."),
        "denied": ("Only the account owner can use this button.", "هذا الزر لصاحب الحساب فقط."),
        "missing": ("That plugin was not found.", "تعذر العثور على الإضافة."),
        "saved": ("Saved. It applies without a restart.", "تم الحفظ. يسري بدون إعادة تشغيل."),
        "readonly": (
            "Change this text with the feature's command.",
            "عدّل هذا النص من أمر الميزة.",
        ),
        "admin-removed": ("Admin removed.", "تمت إزالة المسؤول."),
        "admin-missing": ("That admin was not found.", "هذا المسؤول غير موجود."),
    }
    en, ar = table.get(code, ("Could not update that.", "تعذر التعديل."))
    return pick(language, en, ar)
