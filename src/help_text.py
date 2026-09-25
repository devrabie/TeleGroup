"""Categorized command index and per-command help.

``.الاوامر`` with no arguments is a short numbered index. A section lists one
Arabic command per line (English names when the owner language is English).
A command name opens a card. Pages stay under Telegram's 4096-character limit.
"""

from __future__ import annotations

from src.command_catalog import (
    CATEGORIES,
    PLUGIN_CATEGORY,
    Category,
    category_from_query,
    category_of,
    plugin_label,
)
from src.runtime.plugins import BotCommand, Plugin
from src.templates import apply_prefix, card, command_help_card, field, help_intro, is_ar

# Telegram counts UTF-16 code units. Stay under 4096.
HELP_PAGE_LIMIT = 4000

_EXTRA_PLUGIN_QUERIES: dict[str, tuple[str, ...]] = {
    "delegates": ("المسؤولين", "المسؤولون", "مسؤولو الاوامر"),
}


def _plugins() -> dict[str, Plugin]:
    from src.runtime.plugins import all_plugins

    return {plugin.meta.name: plugin for plugin in all_plugins()}


def _views(account_id: int, language: str) -> list[dict]:
    from src.runtime.gating import list_plugin_views

    return list(list_plugin_views(account_id, language) or [])


def _status(language: str, view: dict) -> str:
    if not view.get("allowed"):
        return "غير مشمولة في الخطة" if is_ar(language) else "Not in your plan"
    if view.get("enabled"):
        return "تعمل" if is_ar(language) else "On"
    return "متوقفة" if is_ar(language) else "Off"


def telegram_units(text: str) -> int:
    """Length the way Telegram counts message text."""
    return len(text.encode("utf-16-le")) // 2


def primary_commands(plugin: Plugin, language: str = "ar") -> list[BotCommand]:
    """One command per action. Arabic help prefers the Arabic name."""
    return [command for _index, command in _primary(plugin, language)]


def _primary(plugin: Plugin, language: str = "ar") -> list[tuple[int, BotCommand]]:
    grouped: dict[str, tuple[int, BotCommand]] = {}
    for index, command in enumerate(plugin.meta.commands):
        current = grouped.get(command.description_en)
        if current is None or _prefer(command, current[1], language):
            grouped[command.description_en] = (index, command)
    return list(grouped.values())


def _usage_names_this(command: BotCommand) -> bool:
    return ("{p}" + command.name) in command.usage(language="en")


def _prefer(candidate: BotCommand, current: BotCommand, language: str) -> bool:
    if language == "ar":
        return current.name.isascii() and not candidate.name.isascii()
    if candidate.name.isascii() and not current.name.isascii():
        return True
    if not candidate.name.isascii() or not current.name.isascii():
        return False
    return _usage_names_this(candidate) and not _usage_names_this(current)


def _alias_line(plugin: Plugin, command: BotCommand, prefix: str) -> str:
    names = [
        f"{prefix}{item.name}"
        for item in plugin.meta.commands
        if item.description_en == command.description_en
    ]
    return " · ".join(names)


def _help_name(language: str) -> str:
    return "الاوامر" if is_ar(language) else "help"


def _panel_name(language: str) -> str:
    return "تحكم" if is_ar(language) else "panel"


def _section_query(category: Category, language: str) -> str:
    if is_ar(language):
        return category.title_ar
    for alias in category.aliases:
        if alias.isascii():
            return alias
    return category.title_en.casefold()


def _count_label(language: str, count: int) -> str:
    if not is_ar(language):
        noun = "command" if count == 1 else "commands"
        return f"({count} {noun})"
    if count == 1:
        return "(امر واحد)"
    if count == 2:
        return "(امرين)"
    if 3 <= count <= 10:
        return f"({count} اوامر)"
    return f"({count} امر)"


def _plugins_in_category(category_id: str) -> list[Plugin]:
    plugins = _plugins()
    ordered: list[Plugin] = []
    seen: set[str] = set()
    for name, cat in PLUGIN_CATEGORY.items():
        plugin = plugins.get(name)
        if cat == category_id and plugin is not None:
            ordered.append(plugin)
            seen.add(name)
    for name, plugin in plugins.items():
        if name not in seen and category_of(name).id == category_id:
            ordered.append(plugin)
    return ordered


def _enabled_count(views: dict[str, dict], category_id: str, language: str) -> int:
    total = 0
    for plugin in _plugins_in_category(category_id):
        view = views.get(plugin.meta.name)
        if not view or not view.get("enabled"):
            continue
        total += len(primary_commands(plugin, language))
    return total


def _tips(language: str, prefix: str) -> str:
    if is_ar(language):
        return "\n".join(
            [
                f"لفتح قسم: {prefix}الاوامر ثم اسمه، مثل {prefix}الاوامر الادارة",
                f"تفاصيل أمر: {prefix}الاوامر حظر",
                f"لوحة الأزرار: {prefix}تحكم",
            ]
        )
    return "\n".join(
        [
            f"Open a section: {prefix}help and its name, for example {prefix}help management",
            f"Command details: {prefix}help ban",
            f"Button panel: {prefix}panel",
        ]
    )


def _who_block(language: str, prefix: str) -> str:
    if is_ar(language):
        return "\n".join(
            [
                "من يرسل الأوامر",
                "● هذا الحساب، أو صاحب الحساب من حسابه، أو مسؤول أضفته",
                f"● إضافة مسؤول: {prefix}رفع ادمن بالرد أو المعرّف أو @username",
                f"● القائمة: {prefix}الادمنية",
                "● الملف والإذاعة وحذف البيانات وإدارة المسؤولين تبقى لصاحب الحساب والحساب نفسه",
            ]
        )
    return "\n".join(
        [
            "Who can send commands",
            "● This account, its owner from their own account, or an admin you added",
            f"● Add an admin: {prefix}addadmin by reply, id, or @username",
            f"● List: {prefix}admins",
            "● Profile, broadcast, deleting saved data, and admin changes "
            "stay with the owner and the account",
        ]
    )


def _panel_usage(language: str, prefix: str) -> str:
    if is_ar(language):
        return f"التشغيل والإيقاف من {prefix}تحكم"
    return f"Turn it on or off from {prefix}panel"


def _usage_label(language: str) -> str:
    return "الاستخدام" if is_ar(language) else "Usage"


def _command_lines(language: str, prefix: str, commands: list[BotCommand]) -> str:
    label = _usage_label(language)
    rows: list[str] = []
    for command in commands:
        usage = apply_prefix(command.usage(language), prefix)
        rows.append(
            f"● {prefix}{command.name} ← {command.description(language)}\n   {label}: {usage}"
        )
    return "\n".join(rows)


def _commandless_block(language: str, prefix: str, plugin: Plugin) -> str:
    label = plugin_label(plugin.meta.name, language)
    usage = _panel_usage(language, prefix)
    return f"● {label} ← {plugin.meta.description(language)}\n   {_usage_label(language)}: {usage}"


def _paused_line(language: str, prefix: str, plugin: Plugin) -> str:
    label = plugin_label(plugin.meta.name, language)
    if plugin.meta.commands:
        state = "متوقفة" if is_ar(language) else "Off"
        return f"⚪️ {label} — {state}"
    state = "متوقفة" if is_ar(language) else "Off"
    return (
        f"⚪️ {label} — {state}. {plugin.meta.description(language)}\n"
        f"   {_usage_label(language)}: {_panel_usage(language, prefix)}"
    )


def _locked_line(language: str, labels: list[str]) -> str:
    if is_ar(language):
        return "🔒 غير متاحة في الخطة: " + "، ".join(labels)
    return "🔒 Not in your plan: " + ", ".join(labels)


def _feature_block(language: str, prefix: str, plugin: Plugin) -> str:
    commands = primary_commands(plugin, language)
    if not commands:
        return _commandless_block(language, prefix, plugin)
    label = plugin_label(plugin.meta.name, language)
    return f"{label}\n{_command_lines(language, prefix, commands)}"


def _continued(language: str, title: str, index: int, total: int) -> str:
    if is_ar(language):
        return f"{title} — تابع {index}/{total}"
    return f"{title} — {index}/{total}"


def pack_help_pages(
    language: str,
    title: str,
    blocks: list[str],
    *,
    limit: int = HELP_PAGE_LIMIT,
) -> list[str]:
    """Wrap blocks in the shared card and split before the Telegram limit."""
    cleaned = [block.strip() for block in blocks if block and block.strip()]
    if not cleaned:
        return [card(language, title, "")]
    long_title = _continued(language, title, 2, 2)
    pages: list[list[str]] = [[]]
    for block in cleaned:
        if _fits(language, long_title, pages[-1] + [block], limit):
            pages[-1].append(block)
            continue
        if pages[-1]:
            pages.append([])
        if _fits(language, long_title, [block], limit):
            pages[-1].append(block)
            continue
        for piece in _split_block(language, long_title, block, limit):
            if pages[-1] and not _fits(language, long_title, pages[-1] + [piece], limit):
                pages.append([])
            pages[-1].append(piece)
    pages = [page for page in pages if page]
    total = len(pages)
    rendered: list[str] = []
    for index, page in enumerate(pages, start=1):
        page_title = title if total == 1 else _continued(language, title, index, total)
        rendered.append(card(language, page_title, "\n\n".join(page)))
    return rendered


def _fits(language: str, title: str, blocks: list[str], limit: int) -> bool:
    if not blocks:
        return True
    text = card(language, title, "\n\n".join(blocks))
    return telegram_units(text) <= limit


def _split_block(language: str, title: str, block: str, limit: int) -> list[str]:
    pieces: list[str] = []
    current: list[str] = []
    for line in block.split("\n"):
        trial = current + [line]
        if _fits(language, title, ["\n".join(trial)], limit):
            current = trial
            continue
        if current:
            pieces.append("\n".join(current))
            current = []
        if _fits(language, title, [line], limit):
            current = [line]
            continue
        start = 0
        while start < len(line):
            end = start + 1
            while end <= len(line) and _fits(language, title, [line[start:end]], limit):
                end += 1
            end = max(start + 1, end - 1)
            pieces.append(line[start:end])
            start = end
    if current:
        pieces.append("\n".join(current))
    return pieces


def render_index(account_id: int, language: str, prefix: str) -> list[str]:
    views = {view["name"]: view for view in _views(account_id, language)}
    lines: list[str] = []
    number = 1
    for category in CATEGORIES:
        if not _plugins_in_category(category.id):
            continue
        count = _enabled_count(views, category.id, language)
        query = _section_query(category, language)
        lines.append(
            f"{number}. {category.emoji} {category.title(language)} ← "
            f"{prefix}{_help_name(language)} {query} {_count_label(language, count)}"
        )
        number += 1
    title = "الأوامر" if is_ar(language) else "Commands"
    blocks = [help_intro(language, prefix)]
    if lines:
        blocks.append("\n".join(lines))
    else:
        empty = "لا توجد أقسام." if is_ar(language) else "There are no sections."
        blocks.append(empty)
    blocks.append(_tips(language, prefix))
    return pack_help_pages(language, title, blocks)


def render_query(account_id: int, language: str, prefix: str, query: str) -> list[str]:
    folded = query.casefold().strip()
    plugins = _plugins()
    views = {view["name"]: view for view in _views(account_id, language)}
    # A section title wins over a command that reuses it, so the index link
    # ``.الاوامر الحماية`` opens the section rather than the PM-guard command.
    category = category_from_query(query)
    if category is not None:
        return _category_pages(language, prefix, category, views)
    command_hit = _find_command(plugins, query, folded)
    if command_hit is not None:
        plugin, command = command_hit
        view = views.get(plugin.meta.name, {})
        return [_command_text(language, prefix, plugin, command, view)]
    plugin_hit = _find_plugin(plugins, query, folded)
    if plugin_hit is not None:
        view = views.get(plugin_hit.meta.name, {})
        return _plugin_pages(language, prefix, plugin_hit, view)
    if is_ar(language):
        return [card(language, "تنبيه", "لا يوجد قسم أو إضافة أو أمر بهذا الاسم.")]
    return [card(language, "Notice", "No section, plugin, or command uses that name.")]


def _find_command(
    plugins: dict[str, Plugin], query: str, folded: str
) -> tuple[Plugin, BotCommand] | None:
    for plugin in plugins.values():
        for command in plugin.meta.commands:
            ascii_match = command.name.isascii() and command.name.casefold() == folded
            if command.name == query or ascii_match:
                return plugin, command
    return None


def _fold_ar(text: str) -> str:
    return text.replace("أ", "ا").replace("إ", "ا").replace("آ", "ا").strip()


def _find_plugin(plugins: dict[str, Plugin], query: str, folded: str) -> Plugin | None:
    folded_ar = _fold_ar(query)
    for plugin in plugins.values():
        if plugin.meta.name == folded or plugin.meta.name == query:
            return plugin
        label_ar = plugin_label(plugin.meta.name, "ar")
        label_en = plugin_label(plugin.meta.name, "en")
        if query == label_ar or folded == label_en.casefold() or folded_ar == _fold_ar(label_ar):
            return plugin
        extras = _EXTRA_PLUGIN_QUERIES.get(plugin.meta.name, ())
        if query in extras or folded_ar in extras:
            return plugin
    return None


def _command_text(
    language: str,
    prefix: str,
    plugin: Plugin,
    command: BotCommand,
    view: dict,
) -> str:
    from src.runtime.actors import access_label

    names = _alias_line(plugin, command, prefix)
    status = _status(language, view) if view else ""
    shown = primary_commands(plugin, language)
    title_command = command
    for item in shown:
        if item.description_en == command.description_en:
            title_command = item
            break
    return command_help_card(
        language,
        prefix=prefix,
        title=f"{prefix}{title_command.name}",
        description=command.description(language),
        usage=apply_prefix(command.usage(language), prefix),
        example=apply_prefix(command.example(language), prefix),
        aliases=names,
        status=status,
        access=access_label(language, command.name),
    )


def _plugin_pages(language: str, prefix: str, plugin: Plugin, view: dict) -> list[str]:
    blocks: list[str] = []
    if plugin.meta.name == "delegates":
        blocks.append(_who_block(language, prefix))
    if view:
        state = _status(language, view)
        blocks.append(field("الحالة" if is_ar(language) else "Status", state))
    blocks.append(_feature_block(language, prefix, plugin))
    title = plugin_label(plugin.meta.name, language)
    return pack_help_pages(language, title, blocks)


def _category_pages(
    language: str,
    prefix: str,
    category: Category,
    views: dict[str, dict],
) -> list[str]:
    blocks: list[str] = []
    paused: list[str] = []
    locked: list[str] = []
    if category.id == "system":
        blocks.append(_who_block(language, prefix))
    for plugin in _plugins_in_category(category.id):
        view = views.get(plugin.meta.name)
        if view is None:
            continue
        if not view.get("allowed"):
            locked.append(plugin_label(plugin.meta.name, language))
            continue
        if not view.get("enabled"):
            paused.append(_paused_line(language, prefix, plugin))
            continue
        blocks.append(_feature_block(language, prefix, plugin))
    if paused:
        blocks.append("\n".join(paused))
    if locked:
        blocks.append(_locked_line(language, locked))
    if not blocks:
        empty = (
            "لا توجد إضافات في هذا القسم." if is_ar(language) else "This section has no plugins."
        )
        blocks.append(empty)
    return pack_help_pages(language, category.label(language), blocks)
