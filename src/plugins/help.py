"""List the plugins enabled for this account."""

from __future__ import annotations

from src.runtime.plugins import BotCommand, CommandContext, Plugin, PluginMeta


class HelpPlugin(Plugin):
    meta = PluginMeta(
        name="help",
        description_en="List the plugins and commands enabled for this account.",
        description_ar="عرض الإضافات والأوامر المفعّلة لهذا الحساب.",
        commands=(
            BotCommand(
                "help",
                "List the plugins and commands enabled for this account.",
                "عرض الإضافات والأوامر المفعّلة لهذا الحساب.",
            ),
            BotCommand(
                "الاوامر",
                "List the plugins and commands enabled for this account.",
                "عرض الإضافات والأوامر المفعّلة لهذا الحساب.",
            ),
        ),
        default_enabled=True,
    )

    async def handle(self, ctx: CommandContext) -> None:
        from src.runtime.gating import list_plugin_views
        from src.runtime.plugins import all_plugins

        query = (ctx.args or "").strip()
        plugins = {plugin.meta.name: plugin for plugin in all_plugins()}
        views = list_plugin_views(ctx.account_id, ctx.language) or []
        enabled = [view for view in views if view["enabled"]]
        if query:
            await self._detail(ctx, query, enabled, plugins)
            return
        if ctx.language == "ar":
            lines = [
                f"الأوامر المفعّلة (البادئة {ctx.prefix})",
                f"للتفاصيل: {ctx.prefix}الاوامر ثم اسم الإضافة",
            ]
            empty = "لا توجد إضافات مفعّلة."
        else:
            lines = [
                f"Enabled commands (prefix {ctx.prefix})",
                f"Details: {ctx.prefix}help and a plugin name",
            ]
            empty = "No plugins are enabled."
        if not enabled:
            lines.append(empty)
            await ctx.reply("\n".join(lines))
            return
        detailed = list(lines)
        for view in enabled:
            detailed.append(f"{view['name']}: {view['description']}")
            if view["commands"]:
                detailed.append(" ".join(f"{ctx.prefix}{name}" for name in view["commands"]))
        text = "\n".join(detailed)
        if len(text) > 3900:
            compact = list(lines)
            if ctx.language == "ar":
                compact.append("القائمة طويلة، وأسماء الأوامر في التفاصيل.")
            else:
                compact.append("The list is long, so command names are in the details.")
            for view in enabled:
                compact.append(f"{view['name']}: {view['description']}")
            text = "\n".join(compact)
            if len(text) > 3900:
                text = text[:3800] + "\n…"
        await ctx.reply(text)

    async def _detail(self, ctx: CommandContext, query: str, enabled: list, plugins: dict) -> None:
        folded = query.casefold()
        match = None
        for view in enabled:
            plugin = plugins.get(view["name"])
            if plugin is None:
                continue
            names = {view["name"].casefold()}
            names.update(command.name.casefold() for command in plugin.meta.commands)
            if folded in names or query in {command.name for command in plugin.meta.commands}:
                match = plugin
                break
        if match is None:
            if ctx.language == "ar":
                await ctx.reply("لا توجد إضافة مفعّلة بهذا الاسم.")
            else:
                await ctx.reply("No enabled plugin uses that name.")
            return
        lines = [match.meta.description(ctx.language)]
        for command in match.meta.commands:
            lines.append(f"{ctx.prefix}{command.name} — {command.description(ctx.language)}")
        await ctx.reply("\n".join(lines))


plugin = HelpPlugin()
