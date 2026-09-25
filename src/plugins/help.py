"""Categorized command index and the inline control panel."""

from __future__ import annotations

from src.help_text import render_index, render_query
from src.panel_open import open_panel
from src.plugins.common import aliases
from src.runtime.plugins import CommandContext, Plugin, PluginMeta
from src.templates import inline_disabled, panel_failed

_PANEL_ARGS = {"لوحة", "اللوحة", "panel"}
_PANEL_COMMANDS = {"تحكم", "اللوحة", "panel"}


class HelpPlugin(Plugin):
    meta = PluginMeta(
        name="help",
        description_en="List commands by section, and open the inline control panel.",
        description_ar="عرض الأوامر حسب القسم، وفتح لوحة التحكم بالأزرار.",
        commands=(
            *aliases(
                "List commands. Add a section, plugin, or command for details.",
                "عرض الأوامر. أضف قسماً أو إضافة أو أمراً للتفاصيل.",
                "help",
                "الاوامر",
                "الأوامر",
            ),
            *aliases(
                "Open the inline button panel in this chat.",
                "فتح لوحة الأزرار في هذه المحادثة.",
                "panel",
                "تحكم",
                "اللوحة",
            ),
        ),
        default_enabled=True,
    )

    async def handle(self, ctx: CommandContext) -> None:
        query = (ctx.args or "").strip()
        panel_arg = query.casefold() in _PANEL_ARGS or query in _PANEL_ARGS
        if ctx.command in _PANEL_COMMANDS or panel_arg:
            await self._panel(ctx)
            return
        if query:
            await self._send(ctx, render_query(ctx.account_id, ctx.language, ctx.prefix, query))
            return
        await self._send(ctx, render_index(ctx.account_id, ctx.language, ctx.prefix))

    async def _send(self, ctx: CommandContext, pages: list[str]) -> None:
        for page in pages:
            if page:
                await ctx.reply(page)

    async def _panel(self, ctx: CommandContext) -> None:
        status = await open_panel(ctx)
        if status == "opened":
            return
        if status == "disabled":
            await ctx.reply(inline_disabled(ctx.language, ctx.prefix))
            return
        await ctx.reply(panel_failed(ctx.language, ctx.prefix))


plugin = HelpPlugin()
