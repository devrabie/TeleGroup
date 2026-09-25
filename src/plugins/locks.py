"""Delete selected message types while this account is a group admin."""

from __future__ import annotations

import logging
from collections.abc import Awaitable

from src.plugins.common import aliases, chat_id_of, is_group_chat, lock_matches, present
from src.plugins.listeners import watch
from src.runtime.plugin_data import add_lock, list_locks, remove_lock
from src.runtime.plugins import AccountSession, CommandContext, Plugin, PluginMeta, is_self_outgoing

log = logging.getLogger(__name__)

_TYPES = {
    "links": "الروابط",
    "media": "الوسائط",
    "photos": "الصور",
    "videos": "الفيديو",
    "stickers": "الملصقات",
    "gifs": "المتحركه",
    "voice": "الصوت",
    "forwards": "التوجيه",
    "text": "الدردشه",
    "all": "الكل",
}
_BY_ALIAS = {name: name for name in _TYPES}
_BY_ALIAS.update({arabic: name for name, arabic in _TYPES.items()})


class LocksPlugin(Plugin):
    meta = PluginMeta(
        name="locks",
        description_en=(
            "Delete links, media, stickers, forwards, and other types in groups you admin."
        ),
        description_ar="حذف الروابط والوسائط والملصقات والتوجيه وغيرها في المجموعات التي تديرها.",
        commands=(
            *aliases(
                "Lock a type: links, media, photos, videos, stickers, "
                "gifs, voice, forwards, text, all.",
                "قفل نوع: الروابط، الوسائط، الصور، الفيديو، الملصقات، "
                "المتحركه، الصوت، التوجيه، الدردشه، الكل.",
                "قفل",
                "lock",
            ),
            *aliases("Unlock a type.", "فتح نوع مقفول.", "فتح", "unlock"),
            *aliases("List locks in this chat.", "عرض أقفال هذه المحادثة.", "الاقفال", "locks"),
        ),
        default_enabled=True,
    )

    def spawn(self, session: AccountSession) -> Awaitable[None]:
        return watch(session, self, on_message)

    async def handle(self, ctx: CommandContext) -> None:
        if not is_group_chat(ctx.message):
            await ctx.reply(present(ctx, "Locks work in groups.", "الأقفال تعمل في المجموعات."))
            return
        chat_id = chat_id_of(ctx.message)
        if not isinstance(chat_id, int):
            await ctx.reply(present(ctx, "This chat has no id.", "هذه المحادثة بلا معرّف."))
            return
        if ctx.command in {"الاقفال", "locks"}:
            names = list_locks(ctx.account_id, chat_id)
            if not names:
                await ctx.reply(present(ctx, "Nothing is locked here.", "لا يوجد قفل هنا."))
                return
            shown = ", ".join(
                _TYPES.get(name, name) if ctx.language == "ar" else name for name in names
            )
            await ctx.reply(present(ctx, f"Locked: {shown}", f"المقفول: {shown}"))
            return
        kind = _BY_ALIAS.get((ctx.args or "").strip().casefold()) or _BY_ALIAS.get(
            (ctx.args or "").strip()
        )
        if kind is None:
            choices = "، ".join(_TYPES.values()) if ctx.language == "ar" else ", ".join(_TYPES)
            await ctx.reply(present(ctx, f"Choose one of: {choices}", f"اختر واحداً من: {choices}"))
            return
        if ctx.command in {"قفل", "lock"}:
            add_lock(ctx.account_id, chat_id, kind)
            await ctx.reply(present(ctx, f"Locked {kind}.", f"تم قفل {_TYPES[kind]}."))
            return
        if remove_lock(ctx.account_id, chat_id, kind):
            await ctx.reply(present(ctx, f"Unlocked {kind}.", f"تم فتح {_TYPES[kind]}."))
        else:
            await ctx.reply(present(ctx, "That type was not locked.", "هذا النوع لم يكن مقفولاً."))


async def on_message(session: AccountSession, message: object) -> None:
    from src.runtime.gating import plugin_is_enabled

    if not plugin_is_enabled(session.account_id, plugin) or is_self_outgoing(message):
        return
    if not is_group_chat(message):
        return
    chat_id = chat_id_of(message)
    message_id = getattr(message, "id", None)
    if not isinstance(chat_id, int) or message_id is None:
        return
    locks = set(list_locks(session.account_id, chat_id))
    if not lock_matches(message, locks):
        return
    try:
        await session.limiter.run(session.client.delete_messages, chat_id, [message_id])
    except Exception:
        log.debug("Lock delete failed for account %s", session.account_id, exc_info=True)


plugin = LocksPlugin()
