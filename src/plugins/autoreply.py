"""Global and per-chat keyword replies."""

from __future__ import annotations

import logging
from collections.abc import Awaitable

from src.plugins.common import aliases, chat_id_of, message_text, present, tr
from src.plugins.listeners import watch
from src.runtime.plugin_data import (
    GLOBAL_CHAT_ID,
    delete_auto_reply,
    list_auto_replies,
    upsert_auto_reply,
)
from src.runtime.plugins import (
    AccountSession,
    CommandContext,
    Plugin,
    PluginMeta,
    SettingField,
    is_self_outgoing,
)

log = logging.getLogger(__name__)


def _split_rule(args: str) -> tuple[str, str] | None:
    for separator in ("|", " - "):
        if separator in args:
            keyword, _, response = args.partition(separator)
            keyword = keyword.strip()
            response = response.strip()
            if keyword and response:
                return keyword, response
    return None


def _norm(keyword: str) -> str:
    text = " ".join(keyword.strip().split())
    if text.isascii():
        return text.casefold()
    return text


def _matches(text: str, keyword: str) -> bool:
    if not text or not keyword:
        return False
    if keyword.isascii():
        return keyword.casefold() in text.casefold()
    return keyword in text


class AutoReplyPlugin(Plugin):
    meta = PluginMeta(
        name="autoreply",
        description_en="Reply to keywords in one chat or in every chat.",
        description_ar="الرد على كلمات في محادثة واحدة أو في كل المحادثات.",
        commands=(
            *aliases(
                "Global reply. Usage: keyword | reply.",
                "رد عام. الاستخدام: الكلمة | الرد.",
                "رد عام",
                "gfilter",
            ),
            *aliases(
                "Delete a global reply.",
                "حذف رد عام.",
                "حذف رد عام",
                "ungfilter",
            ),
            *aliases("List global replies.", "عرض الردود العامة.", "الردود العامة", "gfilters"),
            *aliases(
                "Reply in this chat. Usage: keyword | reply.",
                "رد في هذه المحادثة. الاستخدام: الكلمة | الرد.",
                "رد",
                "filter",
            ),
            *aliases(
                "Delete a reply in this chat.", "حذف رد في هذه المحادثة.", "حذف رد", "unfilter"
            ),
            *aliases("List replies in this chat.", "عرض ردود هذه المحادثة.", "الردود", "filters"),
        ),
        default_enabled=True,
        settings=(SettingField("enabled", "Auto-reply on", "الرد التلقائي يعمل", "bool", True),),
    )

    def spawn(self, session: AccountSession) -> Awaitable[None]:
        return watch(session, self, on_message)

    async def handle(self, ctx: CommandContext) -> None:
        command = ctx.command
        if command in {"رد عام", "gfilter"}:
            await _save(ctx, GLOBAL_CHAT_ID)
        elif command in {"حذف رد عام", "ungfilter"}:
            await _delete(ctx, GLOBAL_CHAT_ID)
        elif command in {"الردود العامة", "gfilters"}:
            await _list(ctx, GLOBAL_CHAT_ID)
        elif command in {"رد", "filter"}:
            await _save(ctx, _chat(ctx))
        elif command in {"حذف رد", "unfilter"}:
            await _delete(ctx, _chat(ctx))
        elif command in {"الردود", "filters"}:
            await _list(ctx, _chat(ctx))


def _chat(ctx: CommandContext) -> int:
    chat_id = chat_id_of(ctx.message)
    if isinstance(chat_id, int):
        return chat_id
    return 0


async def _save(ctx: CommandContext, chat_id: int) -> None:
    if chat_id == 0 and ctx.command in {"رد", "filter"}:
        await ctx.reply(present(ctx, "Open the chat first.", "افتح المحادثة أولاً."))
        return
    parsed = _split_rule(ctx.args or "")
    if parsed is None:
        await ctx.reply(present(ctx, "Usage: keyword | reply", "الاستخدام: الكلمة | الرد"))
        return
    keyword, response = parsed
    result = upsert_auto_reply(ctx.account_id, chat_id, _norm(keyword), response)
    if result == "limit":
        await ctx.reply(
            present(
                ctx,
                "This list is full (100 rules).",
                "القائمة ممتلئة (100 قاعدة).",
            )
        )
        return
    if result != "saved":
        await ctx.reply(present(ctx, "That rule is not valid.", "هذه القاعدة غير صالحة."))
        return
    await ctx.reply(present(ctx, "Reply saved.", "تم حفظ الرد."))


async def _delete(ctx: CommandContext, chat_id: int) -> None:
    keyword = _norm(ctx.args or "")
    if not keyword:
        await ctx.reply(present(ctx, "Send the keyword to delete.", "أرسل الكلمة التي تريد حذفها."))
        return
    if delete_auto_reply(ctx.account_id, chat_id, keyword):
        await ctx.reply(present(ctx, "Reply deleted.", "تم حذف الرد."))
    else:
        await ctx.reply(present(ctx, "No reply uses that keyword.", "لا يوجد رد بهذه الكلمة."))


async def _list(ctx: CommandContext, chat_id: int) -> None:
    rows = list_auto_replies(ctx.account_id, chat_id)
    if not rows:
        await ctx.reply(present(ctx, "No replies saved.", "لا توجد ردود محفوظة."))
        return
    from src.templates import section

    title = section(tr(ctx, "Replies", "الردود"))
    lines = [title]
    for row in rows[:30]:
        lines.append(f"• {row['keyword']} → {row['response'][:80]}")
    await ctx.reply("\n".join(lines))


async def on_message(session: AccountSession, message: object) -> None:
    from src.runtime.gating import plugin_is_enabled
    from src.runtime.plugins import command_prefix

    if not plugin_is_enabled(session.account_id, plugin) or is_self_outgoing(message):
        return
    if not session.settings_for(plugin.meta.name).get("enabled", True):
        return
    text = message_text(message)
    if not text or text.startswith(command_prefix(session.account_id)):
        return
    chat_id = chat_id_of(message)
    if not isinstance(chat_id, int):
        return
    local = list_auto_replies(session.account_id, chat_id)
    chosen = next((row for row in local if _matches(text, row["keyword"])), None)
    if chosen is None:
        global_rows = list_auto_replies(session.account_id, GLOBAL_CHAT_ID)
        chosen = next((row for row in global_rows if _matches(text, row["keyword"])), None)
    if chosen is None:
        return
    from src.runtime.actors import is_trusted_sender

    if is_trusted_sender(session.account_id, message):
        return
    try:
        await session.limiter.run(session.client.send_message, chat_id, chosen["response"])
    except Exception:
        log.debug("Auto-reply failed for account %s", session.account_id, exc_info=True)


plugin = AutoReplyPlugin()
