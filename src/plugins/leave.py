"""Leave the current group or channel."""

from __future__ import annotations

from src.plugins.common import aliases, chat_id_of, chat_kind, tr
from src.runtime.plugins import CommandContext, Plugin, PluginMeta

_CMD = ("مغادرة", "leave")


def can_leave(kind: str, chat_id: object, me_id: object) -> bool:
    if kind not in {"group", "supergroup", "channel", "forum"}:
        return False
    if me_id is not None and chat_id == me_id:
        return False
    return True


class LeavePlugin(Plugin):
    meta = PluginMeta(
        name="leave",
        description_en="Leave the current group or channel.",
        description_ar="مغادرة المجموعة أو القناة الحالية.",
        commands=(
            *aliases(
                "Leave this group or channel.",
                "مغادرة هذه المجموعة أو القناة.",
                *_CMD,
            ),
        ),
        default_enabled=True,
    )

    async def handle(self, ctx: CommandContext) -> None:
        kind = chat_kind(ctx.message)
        chat_id = chat_id_of(ctx.message)
        me = getattr(ctx.client, "me", None)
        if not can_leave(kind, chat_id, getattr(me, "id", None)):
            await ctx.reply(
                tr(ctx, "This command leaves groups and channels.", "هذا الأمر للمجموعات والقنوات.")
            )
            return
        await ctx.reply(tr(ctx, "Leaving.", "جارٍ المغادرة."))
        await ctx.limiter.run(lambda: ctx.client.leave_chat(chat_id))


plugin = LeavePlugin()
