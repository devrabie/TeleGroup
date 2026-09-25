"""Create a group or channel and transfer ownership.

The cloud password is used once and is never stored. The command message that
contains it is deleted. A wrong password is reported without repeating it.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from src.plugins.common import aliases, chat_id_of, is_saved_chat, resolve_user, tr
from src.runtime.plugins import CommandContext, Plugin, PluginMeta

log = logging.getLogger(__name__)

_PENDING_SECONDS = 600
_pending: dict[int, dict[str, Any]] = {}


class CreateChatPlugin(Plugin):
    meta = PluginMeta(
        name="create",
        description_en=(
            "Create a supergroup or channel, then transfer ownership with your 2FA password."
        ),
        description_ar="إنشاء مجموعة خارقة أو قناة، ثم نقل الملكية بكلمة التحقق بخطوتين.",
        commands=(
            *aliases(
                "Create a supergroup. The rest of the line is the title.",
                "إنشاء مجموعة خارقة. بقية السطر هو الاسم.",
                "انشاء كروب",
                "انشاء مجموعة",
                "create group",
                "creategroup",
            ),
            *aliases(
                "Create a channel. The rest of the line is the title.",
                "إنشاء قناة. بقية السطر هو الاسم.",
                "انشاء قناة",
                "create channel",
                "createchannel",
            ),
            *aliases(
                "Start an ownership transfer. Reply to the new owner, or pass @username or id.",
                "بدء نقل الملكية. رد على المالك الجديد أو أرسل @username أو المعرّف.",
                "نقل ملكية",
                "نقل",
                "transfer",
            ),
            *aliases(
                "Send the 2FA password in Saved Messages. The message is deleted.",
                "أرسل كلمة التحقق في الرسائل المحفوظة. تُحذف الرسالة.",
                "كلمة السر",
                "cloudpass",
            ),
        ),
        default_enabled=True,
    )

    async def handle(self, ctx: CommandContext) -> None:
        if ctx.command in {"انشاء كروب", "انشاء مجموعة", "create group", "creategroup"}:
            await _create(ctx, "group")
            return
        if ctx.command in {"انشاء قناة", "create channel", "createchannel"}:
            await _create(ctx, "channel")
            return
        if ctx.command in {"كلمة السر", "cloudpass"}:
            await _password(ctx)
            return
        if ctx.command in {"نقل ملكية", "نقل", "transfer"}:
            await _begin_transfer(ctx)


async def _create(ctx: CommandContext, kind: str) -> None:
    title = (ctx.args or "").strip()
    if not title or len(title) > 128:
        await ctx.reply(tr(ctx, "Send a title after the command.", "أرسل الاسم بعد الأمر."))
        return
    try:
        if kind == "channel":
            chat = await ctx.limiter.run(ctx.client.create_channel, title, "")
        else:
            chat = await ctx.limiter.run(ctx.client.create_supergroup, title, "")
    except Exception:
        log.exception("Create %s failed for account %s", kind, ctx.account_id)
        await ctx.reply(
            tr(ctx, "Telegram refused to create that chat.", "رفض تيليجرام إنشاء المحادثة.")
        )
        return
    chat_id = getattr(chat, "id", None)
    await ctx.reply(tr(ctx, f"Created {title} ({chat_id}).", f"تم إنشاء {title} ({chat_id})."))


async def _begin_transfer(ctx: CommandContext) -> None:
    chat_id = chat_id_of(ctx.message)
    if not isinstance(chat_id, int):
        await ctx.reply(
            tr(ctx, "Run this inside the group or channel.", "نفّذ هذا داخل المجموعة أو القناة.")
        )
        return
    user, error = await resolve_user(ctx)
    if error or user is None:
        await ctx.reply(
            tr(
                ctx,
                "Reply to the new owner, or pass @username or a numeric id.",
                "رد على المالك الجديد، أو أرسل @username أو المعرّف.",
            )
        )
        return
    _pending[ctx.account_id] = {
        "chat_id": chat_id,
        "user_id": user,
        "at": time.monotonic(),
    }
    await ctx.reply(
        tr(
            ctx,
            f"Send {ctx.prefix}cloudpass and the 2FA password in Saved Messages. "
            "That message is deleted and the password is not saved.",
            f"أرسل {ctx.prefix}كلمة السر ثم كلمة التحقق في الرسائل المحفوظة. "
            "تُحذف الرسالة ولا تُحفظ كلمة السر.",
        )
    )


async def _password(ctx: CommandContext) -> None:
    password = (ctx.args or "").strip()
    await _delete_command(ctx)
    if not is_saved_chat(ctx.message, ctx.client):
        await ctx.reply(
            tr(
                ctx,
                "Send the password in Saved Messages only.",
                "أرسل كلمة السر في الرسائل المحفوظة فقط.",
            )
        )
        return
    pending = _pending.get(ctx.account_id)
    if pending is None or time.monotonic() - float(pending["at"]) > _PENDING_SECONDS:
        _pending.pop(ctx.account_id, None)
        await ctx.reply(tr(ctx, "No transfer is waiting.", "لا يوجد نقل ملكية بانتظار التأكيد."))
        return
    if not password:
        await ctx.reply(
            tr(
                ctx,
                "The password was empty. Start the transfer again.",
                "كلمة السر فارغة. ابدأ النقل من جديد.",
            )
        )
        return
    _pending.pop(ctx.account_id, None)
    try:
        await ctx.limiter.run(
            ctx.client.transfer_chat_ownership,
            pending["chat_id"],
            pending["user_id"],
            password,
        )
    except Exception:
        log.warning("Ownership transfer failed for account %s", ctx.account_id)
        await ctx.reply(
            tr(
                ctx,
                "Transfer failed. The password was rejected or this account is not the owner.",
                "فشل النقل. رُفضت كلمة السر أو أن هذا الحساب ليس المالك.",
            )
        )
        return
    await ctx.reply(tr(ctx, "Ownership transferred.", "تم نقل الملكية."))


async def _delete_command(ctx: CommandContext) -> None:
    chat_id = chat_id_of(ctx.message)
    message_id = getattr(ctx.message, "id", None)
    if chat_id is None or message_id is None:
        return
    try:
        await ctx.limiter.run(ctx.client.delete_messages, chat_id, message_id)
    except Exception:
        log.warning("Could not delete the password message for account %s", ctx.account_id)


plugin = CreateChatPlugin()
