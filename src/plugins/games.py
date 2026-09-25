"""Opt-in notice when a bot posts in a chosen group.

ZThon-style auto-play answers game prompts in the group to farm rewards. That
races other people and is the kind of unattended user-account automation
Telegram treats as abuse. This plugin never posts into the group. The owner
turns it on per chat, and bot posts are copied as a notice to Saved Messages
after a cooldown so the owner can answer by hand.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable

from src.plugins.common import aliases, chat_id_of, is_group_chat, present
from src.plugins.listeners import watch
from src.runtime.plugins import (
    AccountSession,
    CommandContext,
    Plugin,
    PluginMeta,
    SettingField,
    is_self_outgoing,
)

log = logging.getLogger(__name__)

_MIN_COOLDOWN = 60


class GamesPlugin(Plugin):
    meta = PluginMeta(
        name="games",
        description_en=(
            "Tell Saved Messages when a bot posts in a group you opted into. It does not play."
        ),
        description_ar="يبلّغ الرسائل المحفوظة عندما ينشر بوت في مجموعة فعّلتها. لا يلعب بدلاً عنك.",
        commands=(
            *aliases(
                "Watch this group and notify Saved Messages. It does not answer the game.",
                "مراقبة هذه المجموعة وإبلاغ الرسائل المحفوظة. لا يجيب على اللعبة.",
                "تفعيل اللعبة",
                "gamewatch",
            ),
            *aliases(
                "Stop watching this group.",
                "إيقاف مراقبة هذه المجموعة.",
                "ايقاف اللعبة",
                "gameoff",
            ),
        ),
        default_enabled=True,
        settings=(
            SettingField(
                "cooldown",
                "Seconds between notices",
                "الثواني بين التنبيهات",
                "int",
                120,
                _MIN_COOLDOWN,
                3600,
            ),
        ),
    )

    def spawn(self, session: AccountSession) -> Awaitable[None]:
        return watch(session, self, on_message)

    async def handle(self, ctx: CommandContext) -> None:
        if not is_group_chat(ctx.message):
            await ctx.reply(
                present(
                    ctx,
                    "Turn this on inside the group.",
                    "فعّل هذا داخل المجموعة.",
                )
            )
            return
        chat_id = chat_id_of(ctx.message)
        if not isinstance(chat_id, int):
            await ctx.reply(present(ctx, "This chat has no id.", "هذه المحادثة بلا معرّف."))
            return
        chats = [int(item) for item in (ctx.settings.get("chats") or [])]
        enable = ctx.command in {"تفعيل اللعبة", "gamewatch"}
        if enable and chat_id not in chats:
            chats.append(chat_id)
        if not enable:
            chats = [item for item in chats if item != chat_id]
        ctx.settings.set("chats", chats)
        if enable:
            await ctx.reply(
                present(
                    ctx,
                    "Watching this group. Notices go to Saved Messages only. "
                    "The account will not answer.",
                    "تتم مراقبة هذه المجموعة. التنبيهات تذهب للرسائل المحفوظة فقط. الحساب لن يجيب.",
                )
            )
        else:
            await ctx.reply(
                present(
                    ctx,
                    "Stopped watching this group.",
                    "توقفت مراقبة هذه المجموعة.",
                )
            )


async def on_message(session: AccountSession, message: object) -> None:
    from src.runtime.gating import plugin_is_enabled

    if not plugin_is_enabled(session.account_id, plugin) or is_self_outgoing(message):
        return
    sender = getattr(message, "from_user", None)
    if sender is None or not getattr(sender, "is_bot", False):
        return
    if not is_group_chat(message):
        return
    chat_id = chat_id_of(message)
    if not isinstance(chat_id, int):
        return
    settings = session.settings_for(plugin.meta.name)
    chats = [int(item) for item in (settings.get("chats") or [])]
    if chat_id not in chats:
        return
    cooldown = int(settings.get("cooldown", 120) or 120)
    cooldown = max(_MIN_COOLDOWN, cooldown)
    seen = dict(settings.get("seen") or {})
    last = float(seen.get(str(chat_id), 0) or 0)
    now = time.time()
    if now - last < cooldown:
        return
    seen[str(chat_id)] = now
    settings.set("seen", seen)
    from src.templates import SEP, section

    title = getattr(getattr(message, "chat", None), "title", None) or chat_id
    text = str(getattr(message, "text", None) or getattr(message, "caption", None) or "")
    notice = (
        f"{section('🎮 لعبة')}\n{SEP}\nGame bot in {title} ({chat_id}).\n{text[:300]}"
    ).strip()
    try:
        await session.limiter.run(session.client.send_message, "me", notice)
    except Exception:
        log.debug("Game notice failed for account %s", session.account_id, exc_info=True)


plugin = GamesPlugin()
