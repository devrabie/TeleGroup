"""Away mode. Clears itself when the account sends a normal message."""

from __future__ import annotations

import logging
from collections.abc import Awaitable
from datetime import UTC, datetime

from src.plugins.common import aliases, present
from src.plugins.listeners import watch
from src.runtime.plugins import (
    AccountSession,
    CommandContext,
    Plugin,
    PluginMeta,
    SettingField,
    command_prefix,
    is_self_outgoing,
    resolve_command,
)

log = logging.getLogger(__name__)

_MAX_TOLD = 400


class AfkPlugin(Plugin):
    meta = PluginMeta(
        name="afk",
        description_en="Reply once while you are away, then turn off when you send a message.",
        description_ar="يرد مرة واحدة أثناء غيابك، ثم يتوقف عندما ترسل رسالة.",
        commands=(
            *aliases(
                "Turn away mode on. Optional custom message.",
                "تشغيل وضع الغياب. يمكن إرفاق رسالة.",
                "غائب",
                "afk",
            ),
            *aliases("Turn away mode off.", "إيقاف وضع الغياب.", "الغاء الغياب", "unafk"),
        ),
        default_enabled=True,
        settings=(
            SettingField(
                "message",
                "Away message",
                "رسالة الغياب",
                "str",
                "I'm away right now.",
            ),
        ),
    )

    def spawn(self, session: AccountSession) -> Awaitable[None]:
        return watch(session, self, on_message)

    async def handle(self, ctx: CommandContext) -> None:
        if ctx.command in {"الغاء الغياب", "unafk"}:
            _clear(ctx.settings)
            await ctx.reply(present(ctx, "Away mode is off.", "تم إيقاف وضع الغياب."))
            return
        custom = (ctx.args or "").strip()
        message = custom or str(ctx.settings.get("message") or "I'm away right now.")
        ctx.settings.set("active", True)
        ctx.settings.set("message", message)
        ctx.settings.set("since", datetime.now(UTC).isoformat())
        ctx.settings.set("told", [])
        await ctx.reply(present(ctx, "Away mode is on.", "تم تشغيل وضع الغياب."))


def _clear(settings) -> None:
    settings.set("active", False)
    settings.set("told", [])


async def on_message(session: AccountSession, message: object) -> None:
    from src.runtime.gating import plugin_is_enabled

    if not plugin_is_enabled(session.account_id, plugin):
        return
    settings = session.settings_for(plugin.meta.name)
    if not settings.get("active", False):
        return
    text = str(getattr(message, "text", None) or "")
    if is_self_outgoing(message):
        prefix = command_prefix(session.account_id)
        if resolve_command(text, prefix) is not None:
            return
        _clear(settings)
        try:
            chat = getattr(message, "chat", None)
            chat_id = getattr(chat, "id", None) or "me"
            await session.limiter.run(
                session.client.send_message,
                chat_id,
                "Away mode is off."
                if session.details.get("language_code") != "ar"
                else "تم إلغاء الغياب.",
            )
        except Exception:
            log.debug("AFK clear notice failed", exc_info=True)
        return
    from src.runtime.actors import is_trusted_sender

    if is_trusted_sender(session.account_id, message):
        return
    sender = getattr(message, "from_user", None)
    user_id = getattr(sender, "id", None)
    if user_id is None or getattr(sender, "is_bot", False):
        return
    told = list(settings.get("told") or [])
    if user_id in told:
        return
    told.append(user_id)
    settings.set("told", told[-_MAX_TOLD:])
    chat = getattr(message, "chat", None)
    chat_id = getattr(chat, "id", None)
    if chat_id is None:
        return
    notice = str(settings.get("message") or "I'm away right now.")
    try:
        await session.limiter.run(session.client.send_message, chat_id, notice)
    except Exception:
        log.debug("AFK reply failed for account %s", session.account_id, exc_info=True)


plugin = AfkPlugin()
