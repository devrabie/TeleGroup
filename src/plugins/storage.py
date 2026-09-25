"""Copy private messages and mentions into a log chat."""

from __future__ import annotations

import logging
from collections.abc import Awaitable

from src.plugins.common import aliases, chat_id_of, chat_kind, is_saved_chat, present
from src.plugins.listeners import watch
from src.runtime.plugins import (
    AccountSession,
    CommandContext,
    Plugin,
    PluginMeta,
    SettingField,
    command_prefix,
    is_self_outgoing,
)

log = logging.getLogger(__name__)

_STATUS = ("تخزين", "storage")
_SET = ("وضع التخزين", "setlog")


class StoragePlugin(Plugin):
    meta = PluginMeta(
        name="storage",
        description_en="Copy private messages and mentions into a log chat.",
        description_ar="نسخ الرسائل الخاصة والإشارات إلى محادثة سجل.",
        commands=(
            *aliases(
                "Show, enable, or disable logging. Args: on, off.",
                "عرض التخزين أو تشغيله أو إيقافه. المعطيات: تشغيل، ايقاف.",
                *_STATUS,
            ),
            *aliases(
                "Set the log chat: here, me, or a chat id.",
                "تعيين محادثة السجل: هنا، محفوظات، أو معرّف.",
                *_SET,
            ),
        ),
        default_enabled=True,
        settings=(
            SettingField("enabled", "Logging on", "التخزين يعمل", "bool", False),
            SettingField(
                "log_chat", "Log chat (me or id)", "محادثة السجل (me أو معرّف)", "str", "me"
            ),
            SettingField("log_private", "Log private chats", "تسجيل الخاص", "bool", True),
            SettingField("log_mentions", "Log mentions", "تسجيل الإشارات", "bool", True),
        ),
    )

    def spawn(self, session: AccountSession) -> Awaitable[None]:
        return watch(session, self, on_message)

    async def handle(self, ctx: CommandContext) -> None:
        if ctx.command in _SET:
            await _set_log(ctx)
            return
        arg = (ctx.args or "").strip().casefold()
        if arg in {"on", "تشغيل", "1"}:
            ctx.settings.set("enabled", True)
            await ctx.reply(present(ctx, "Logging is on.", "تم تشغيل التخزين."))
            return
        if arg in {"off", "ايقاف", "0"}:
            ctx.settings.set("enabled", False)
            await ctx.reply(present(ctx, "Logging is off.", "تم إيقاف التخزين."))
            return
        enabled = bool(ctx.settings.get("enabled", False))
        log_chat = ctx.settings.get("log_chat", "me")
        state = "on" if enabled else "off"
        state_ar = "يعمل" if enabled else "متوقف"
        await ctx.reply(
            present(
                ctx,
                f"Logging is {state}. Log chat: {log_chat}.",
                f"التخزين {state_ar}. محادثة السجل: {log_chat}.",
            )
        )


async def _set_log(ctx: CommandContext) -> None:
    arg = (ctx.args or "").strip()
    folded = arg.casefold()
    if folded in {"here", "هنا"}:
        target = chat_id_of(ctx.message)
    elif folded in {"me", "محفوظات", "saved", ""}:
        target = "me"
    elif arg.lstrip("-").isdigit():
        target = int(arg)
    else:
        await ctx.reply(
            present(
                ctx,
                "Use here, me, or a numeric chat id.",
                "استخدم هنا أو محفوظات أو معرّف محادثة.",
            )
        )
        return
    ctx.settings.set("log_chat", target)
    ctx.settings.set("enabled", True)
    await ctx.reply(present(ctx, f"Log chat set to {target}.", f"تم تعيين السجل إلى {target}."))


async def on_message(session: AccountSession, message: object) -> None:
    from src.runtime.gating import plugin_is_enabled

    if not plugin_is_enabled(session.account_id, plugin):
        return
    if is_self_outgoing(message):
        return
    settings = session.settings_for(plugin.meta.name)
    if not settings.get("enabled", False):
        return
    kind = chat_kind(message)
    private = kind == "private" and not is_saved_chat(message, session.client)
    mentioned = bool(getattr(message, "mentioned", False))
    if private and not settings.get("log_private", True):
        return
    if mentioned and not private and not settings.get("log_mentions", True):
        return
    if not private and not mentioned:
        return
    log_chat = settings.get("log_chat", "me") or "me"
    source = chat_id_of(message)
    message_id = getattr(message, "id", None)
    if source is None or message_id is None or source == log_chat:
        return
    prefix = command_prefix(session.account_id)
    if str(getattr(message, "text", "") or "").startswith(str(prefix)):
        return
    try:
        await session.limiter.run(session.client.forward_messages, log_chat, source, message_id)
    except Exception:
        log.debug("Storage forward failed for account %s", session.account_id, exc_info=True)


plugin = StoragePlugin()
