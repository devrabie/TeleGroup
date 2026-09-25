"""Warn unknown private senders, then block them."""

from __future__ import annotations

import logging
from collections.abc import Awaitable

from src.plugins.common import (
    aliases,
    chat_id_of,
    chat_kind,
    is_saved_chat,
    present,
    resolve_user,
)
from src.plugins.listeners import watch
from src.runtime.plugin_data import get_pm_permit, set_pm_permit
from src.runtime.plugins import (
    AccountSession,
    CommandContext,
    Plugin,
    PluginMeta,
    SettingField,
    is_self_outgoing,
)

log = logging.getLogger(__name__)

_DEFAULT_WARN = (
    "This account only accepts approved private messages. Keep messaging and you will be blocked."
)


class PmPermitPlugin(Plugin):
    meta = PluginMeta(
        name="pmpermit",
        description_en="Approve private chats, warn strangers, and block them after a limit.",
        description_ar="قبول الخاص، تحذير الغرباء، وحظرهم بعد حد التحذيرات.",
        commands=(
            *aliases(
                "Show or switch PM protection. Args: on, off.",
                "عرض حماية الخاص أو تبديلها. المعطيات: تشغيل، ايقاف.",
                "الحماية",
                "pmpermit",
            ),
            *aliases(
                "Approve a user by reply, @username, or id.",
                "السماح لمستخدم بالرد أو @username أو المعرّف.",
                "سماح",
                "approve",
            ),
            *aliases(
                "Remove approval and reset warnings.",
                "رفض المستخدم وتصفير التحذيرات.",
                "رفض",
                "disapprove",
            ),
            *aliases(
                "Set the warning limit (1-10).",
                "تعيين حد التحذير (1-10).",
                "عدد التحذير",
                "pmwarn",
            ),
        ),
        default_enabled=True,
        settings=(
            SettingField("enabled", "PM protection", "حماية الخاص", "bool", False),
            SettingField(
                "warn_limit", "Warnings before block", "التحذيرات قبل الحظر", "int", 3, 1, 10
            ),
            SettingField("message", "Warning text", "نص التحذير", "str", _DEFAULT_WARN),
        ),
    )

    def spawn(self, session: AccountSession) -> Awaitable[None]:
        return watch(session, self, on_message)

    async def handle(self, ctx: CommandContext) -> None:
        if ctx.command in {"سماح", "approve"}:
            await _set_approval(ctx, approved=True)
            return
        if ctx.command in {"رفض", "disapprove"}:
            await _set_approval(ctx, approved=False)
            return
        if ctx.command in {"عدد التحذير", "pmwarn"}:
            await _set_limit(ctx)
            return
        arg = (ctx.args or "").strip().casefold()
        if arg in {"on", "تشغيل"}:
            ctx.settings.set("enabled", True)
            await ctx.reply(present(ctx, "PM protection is on.", "تم تشغيل حماية الخاص."))
            return
        if arg in {"off", "ايقاف"}:
            ctx.settings.set("enabled", False)
            await ctx.reply(present(ctx, "PM protection is off.", "تم إيقاف حماية الخاص."))
            return
        enabled = bool(ctx.settings.get("enabled", False))
        limit = int(ctx.settings.get("warn_limit", 3) or 3)
        state = "on" if enabled else "off"
        state_ar = "تعمل" if enabled else "متوقفة"
        await ctx.reply(
            present(
                ctx,
                f"PM protection is {state}. Block after {limit} warnings.",
                f"حماية الخاص {state_ar}. الحظر بعد {limit} تحذيرات.",
            )
        )


async def _user_id(ctx: CommandContext) -> int | None:
    if chat_kind(ctx.message) == "private":
        reply = getattr(ctx.message, "reply_to_message", None)
        user = getattr(reply, "from_user", None) if reply is not None else None
        if user is not None and getattr(user, "id", None):
            return int(user.id)
        chat_id = chat_id_of(ctx.message)
        me = getattr(ctx.client, "me", None)
        if isinstance(chat_id, int) and chat_id != getattr(me, "id", None):
            if not (ctx.args or "").strip():
                return chat_id
    found, error = await resolve_user(ctx)
    if error or not isinstance(found, int):
        return None
    return found


async def _set_approval(ctx: CommandContext, *, approved: bool) -> None:
    user_id = await _user_id(ctx)
    if user_id is None:
        await ctx.reply(
            present(
                ctx,
                "Reply to the user or pass their numeric id.",
                "رد على المستخدم أو أرسل معرّفه.",
            )
        )
        return
    set_pm_permit(
        ctx.account_id,
        user_id,
        approved=approved,
        warnings=0,
        blocked=False,
    )
    if approved:
        await ctx.reply(present(ctx, "Approved.", "تم السماح."))
    else:
        await ctx.reply(present(ctx, "Approval removed.", "تم الرفض."))


async def _set_limit(ctx: CommandContext) -> None:
    raw = (ctx.args or "").strip()
    try:
        value = int(raw)
    except ValueError:
        value = 0
    if value < 1 or value > 10:
        await ctx.reply(present(ctx, "Pick a number from 1 to 10.", "اختر رقماً من 1 إلى 10."))
        return
    ctx.settings.set("warn_limit", value)
    await ctx.reply(present(ctx, f"Warning limit is {value}.", f"حد التحذير هو {value}."))


async def on_message(session: AccountSession, message: object) -> None:
    from src.runtime.gating import plugin_is_enabled

    if not plugin_is_enabled(session.account_id, plugin) or is_self_outgoing(message):
        return
    settings = session.settings_for(plugin.meta.name)
    if not settings.get("enabled", False):
        return
    if chat_kind(message) != "private" or is_saved_chat(message, session.client):
        return
    sender = getattr(message, "from_user", None)
    user_id = getattr(sender, "id", None)
    if user_id is None or getattr(sender, "is_self", False) or getattr(sender, "is_bot", False):
        return
    user_id = int(user_id)
    record = get_pm_permit(session.account_id, user_id) or {
        "approved": False,
        "warnings": 0,
        "blocked": False,
    }
    if record["approved"] or record["blocked"]:
        return
    limit = int(settings.get("warn_limit", 3) or 3)
    warnings = int(record["warnings"]) + 1
    blocked = warnings >= limit
    set_pm_permit(
        session.account_id,
        user_id,
        approved=False,
        warnings=warnings,
        blocked=blocked,
    )
    chat_id = chat_id_of(message)
    if chat_id is None:
        return
    try:
        if blocked:
            await session.limiter.run(session.client.block_user, user_id)
            language = str(session.details.get("language_code") or "en")
            notice = "تم حظرك." if language == "ar" else "You were blocked."
            await session.limiter.run(session.client.send_message, chat_id, notice)
        else:
            text = str(settings.get("message") or _DEFAULT_WARN)
            await session.limiter.run(
                session.client.send_message,
                chat_id,
                f"{text}\n({warnings}/{limit})",
            )
    except Exception:
        log.debug("PM permit action failed for account %s", session.account_id, exc_info=True)


plugin = PmPermitPlugin()
