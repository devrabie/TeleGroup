"""Show a user or the current chat."""

from __future__ import annotations

from typing import Any

from src.plugins.common import aliases, chat_id_of, resolve_user, tr
from src.runtime.plugins import CommandContext, Plugin, PluginMeta

_CMD = ("معلومات", "info")


def _value(raw: Any) -> str:
    if raw is None:
        return ""
    value = getattr(raw, "value", raw)
    return str(value).split(".")[-1]


def format_chat_info(chat: Any, language: str) -> str:
    first = getattr(chat, "first_name", None) or ""
    last = getattr(chat, "last_name", None) or ""
    title = getattr(chat, "title", None) or (f"{first} {last}").strip()
    username = getattr(chat, "username", None) or ""
    bio = getattr(chat, "bio", None) or getattr(chat, "description", None) or ""
    members = getattr(chat, "members_count", None)
    rows = [
        ("Id", "المعرّف", getattr(chat, "id", "")),
        ("Type", "النوع", _value(getattr(chat, "type", ""))),
        ("Name", "الاسم", title),
        ("Username", "المستخدم", f"@{username}" if username else ""),
        ("Members", "الأعضاء", members if members else ""),
        ("About", "نبذة", str(bio)[:400]),
    ]
    lines = []
    for en, ar, value in rows:
        if value in ("", None):
            continue
        label = ar if language == "ar" else en
        lines.append(f"{label}: {value}")
    return "\n".join(lines) or ("لا توجد معلومات." if language == "ar" else "No details.")


class InfoPlugin(Plugin):
    meta = PluginMeta(
        name="info",
        description_en="Show the current chat, or a user from a reply, @username, or id.",
        description_ar="عرض المحادثة الحالية أو مستخدماً بالرد أو @username أو المعرّف.",
        commands=(
            *aliases(
                "Show this chat, or a user from a reply, @username, or id.",
                "عرض هذه المحادثة أو مستخدم بالرد أو @username أو المعرّف.",
                *_CMD,
            ),
        ),
        default_enabled=True,
    )

    async def handle(self, ctx: CommandContext) -> None:
        reply = getattr(ctx.message, "reply_to_message", None)
        target: Any = None
        if reply is not None or (ctx.args or "").strip():
            target, error = await resolve_user(ctx)
            if error:
                await ctx.reply(
                    tr(ctx, "Reply, or send @username or an id.", "رد، أو أرسل @username أو معرّفاً.")
                )
                return
        else:
            target = chat_id_of(ctx.message)
        if target is None:
            await ctx.reply(tr(ctx, "No chat to show.", "لا توجد محادثة للعرض."))
            return
        chat = await ctx.limiter.run(lambda: ctx.client.get_chat(target))
        await ctx.reply(format_chat_info(chat, ctx.language))


plugin = InfoPlugin()
