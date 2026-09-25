"""Show the account and chat ids."""

from __future__ import annotations

from src.plugins.common import aliases
from src.runtime.plugins import CommandContext, Plugin, PluginMeta
from src.templates import card


class IdentifyPlugin(Plugin):
    meta = PluginMeta(
        name="id",
        description_en="Show the user id and the current chat id.",
        description_ar="عرض معرّف المستخدم ومعرّف المحادثة الحالية.",
        commands=(
            *aliases(
                "Show the user id and the current chat id.",
                "عرض معرّف المستخدم ومعرّف المحادثة الحالية.",
                "ايدي",
                "id",
            ),
        ),
        default_enabled=True,
    )

    async def handle(self, ctx: CommandContext) -> None:
        me = getattr(ctx.client, "me", None)
        user_id = getattr(me, "id", None)
        if user_id is None:
            sender = getattr(ctx.message, "from_user", None)
            user_id = getattr(sender, "id", None)
        chat = getattr(ctx.message, "chat", None)
        chat_id = getattr(chat, "id", None)
        if ctx.language == "ar":
            title = "المعرّف"
            text = f"المستخدم: `{user_id}`\nالمحادثة: `{chat_id}`"
        else:
            title = "Id"
            text = f"User: `{user_id}`\nChat: `{chat_id}`"
        await ctx.reply(card(ctx.language, title, text))


plugin = IdentifyPlugin()
