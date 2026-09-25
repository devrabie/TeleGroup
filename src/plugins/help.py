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

        views = list_plugin_views(ctx.account_id, ctx.language) or []
        enabled = [view for view in views if view["enabled"]]
        if ctx.language == "ar":
            lines = [f"الأوامر المفعّلة (البادئة {ctx.prefix})"]
            empty = "لا توجد إضافات مفعّلة."
        else:
            lines = [f"Enabled commands (prefix {ctx.prefix})"]
            empty = "No plugins are enabled."
        if not enabled:
            lines.append(empty)
        for view in enabled:
            lines.append(f"{view['name']}: {view['description']}")
            if view["commands"]:
                lines.append(" ".join(f"{ctx.prefix}{name}" for name in view["commands"]))
        await ctx.reply("\n".join(lines))


plugin = HelpPlugin()
