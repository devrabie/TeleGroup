"""Categorized command index and per-command help."""

from __future__ import annotations

from src.command_catalog import (
    CATEGORIES,
    category_from_query,
    category_of,
    plugin_label,
)
from src.runtime.plugins import BotCommand, Plugin
from src.templates import apply_prefix, card, command_help_card, field, help_intro, is_ar, section


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


def primary_commands(plugin: Plugin) -> list[BotCommand]:
    """One command per action, preferring the Arabic name."""
    return [command for _index, command in _primary(plugin)]


def _primary(plugin: Plugin) -> list[tuple[int, BotCommand]]:
    """One command per description, preferring the Arabic name."""
    grouped: dict[str, tuple[int, BotCommand]] = {}
    for index, command in enumerate(plugin.meta.commands):
        current = grouped.get(command.description_en)
        if current is None or (current[1].name.isascii() and not command.name.isascii()):
            grouped[command.description_en] = (index, command)
    return list(grouped.values())


def _alias_line(plugin: Plugin, command: BotCommand, prefix: str) -> str:
    names = [
        f"{prefix}{item.name}"
        for item in plugin.meta.commands
        if item.description_en == command.description_en
    ]
    return " · ".join(names)


def render_index(account_id: int, language: str, prefix: str) -> str:
    plugins = _plugins()
    views = [view for view in _views(account_id, language) if view.get("enabled")]
    by_category: dict[str, list[dict]] = {item.id: [] for item in CATEGORIES}
    for view in views:
        by_category.setdefault(category_of(view["name"]).id, []).append(view)
    chunks = [help_intro(language, prefix)]
    for category in CATEGORIES:
        rows = by_category.get(category.id) or []
        if not rows:
            continue
        lines = [section(category.label(language))]
        for view in rows:
            plugin = plugins.get(view["name"])
            label = plugin_label(view["name"], language)
            if plugin is None:
                lines.append(field(label, view["description"]))
                continue
            names = " ".join(f"{prefix}{command.name}" for command in plugin.meta.commands)
            lines.append(f"● {label}")
            lines.append(names)
        chunks.append("\n".join(lines))
    if len(views) == 0:
        empty = "لا توجد إضافات مفعّلة." if is_ar(language) else "No plugins are enabled."
        chunks.append(empty)
    text = "\n\n".join(chunks)
    if len(text) <= 3900:
        return text
    compact = [help_intro(language, prefix)]
    for category in CATEGORIES:
        rows = by_category.get(category.id) or []
        if not rows:
            continue
        lines = [section(category.label(language))]
        for view in rows:
            plugin = plugins.get(view["name"])
            if plugin is None:
                continue
            lines.append(" ".join(f"{prefix}{command.name}" for command in plugin.meta.commands))
        compact.append("\n".join(lines))
    text = "\n\n".join(compact)
    if len(text) > 3900:
        return text[:3800] + "\n…"
    return text


def render_query(account_id: int, language: str, prefix: str, query: str) -> str:
    folded = query.casefold().strip()
    plugins = _plugins()
    views = {view["name"]: view for view in _views(account_id, language)}
    command_hit = _find_command(plugins, query, folded)
    if command_hit is not None:
        plugin, command = command_hit
        view = views.get(plugin.meta.name, {})
        return _command_text(language, prefix, plugin, command, view)
    plugin_hit = _find_plugin(plugins, query, folded)
    if plugin_hit is not None:
        view = views.get(plugin_hit.meta.name, {})
        return _plugin_text(language, prefix, plugin_hit, view)
    category = category_from_query(query)
    if category is not None:
        return _category_text(language, prefix, category.id, plugins, views)
    if is_ar(language):
        return card(language, "تنبيه", "لا يوجد قسم أو إضافة أو أمر بهذا الاسم.")
    return card(language, "Notice", "No section, plugin, or command uses that name.")


def _find_command(
    plugins: dict[str, Plugin], query: str, folded: str
) -> tuple[Plugin, BotCommand] | None:
    for plugin in plugins.values():
        for command in plugin.meta.commands:
            ascii_match = command.name.isascii() and command.name.casefold() == folded
            if command.name == query or ascii_match:
                return plugin, command
    return None


def _find_plugin(plugins: dict[str, Plugin], query: str, folded: str) -> Plugin | None:
    for plugin in plugins.values():
        if plugin.meta.name == folded or plugin.meta.name == query:
            return plugin
        label_ar = plugin_label(plugin.meta.name, "ar")
        label_en = plugin_label(plugin.meta.name, "en")
        if query == label_ar or folded == label_en.casefold():
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
    return command_help_card(
        language,
        prefix=prefix,
        title=f"{prefix}{command.name}",
        description=command.description(language),
        usage=apply_prefix(command.usage(language), prefix),
        example=apply_prefix(command.example(language), prefix),
        aliases=names,
        status=status,
        access=access_label(language, command.name),
    )


def _plugin_text(language: str, prefix: str, plugin: Plugin, view: dict) -> str:
    lines = [plugin.meta.description(language)]
    if view:
        lines.append(field("الحالة" if is_ar(language) else "Status", _status(language, view)))
    for _index, command in _primary(plugin):
        usage = apply_prefix(command.usage(language), prefix)
        lines.append(field(f"{prefix}{command.name}", f"{command.description(language)} — {usage}"))
    title = plugin_label(plugin.meta.name, language)
    return card(language, title, "\n".join(lines))


def _category_text(
    language: str,
    prefix: str,
    category_id: str,
    plugins: dict[str, Plugin],
    views: dict[str, dict],
) -> str:
    from src.command_catalog import CATEGORY_BY_ID

    category = CATEGORY_BY_ID[category_id]
    lines: list[str] = []
    for name, plugin in plugins.items():
        if category_of(name).id != category_id:
            continue
        view = views.get(name)
        if view is None:
            continue
        label = plugin_label(name, language)
        state = _status(language, view)
        lines.append(field(label, state))
        shown = " ".join(f"{prefix}{command.name}" for _index, command in _primary(plugin))
        if shown:
            lines.append(shown)
    if not lines:
        empty = (
            "لا توجد إضافات في هذا القسم." if is_ar(language) else "This section has no plugins."
        )
        lines.append(empty)
    hint = (
        f"التفاصيل: {prefix}الاوامر ثم اسم الإضافة"
        if is_ar(language)
        else f"Details: {prefix}help and the plugin name"
    )
    lines.append(hint)
    return card(language, category.label(language), "\n".join(lines))
