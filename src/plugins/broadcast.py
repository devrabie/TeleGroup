"""Send one text to groups or private chats, with confirmation and a stop command."""

from __future__ import annotations

import logging
from typing import Any

from src.plugins.common import aliases, present
from src.plugins.jobs import begin_job, cancel_job, finish_job
from src.runtime.plugins import CommandContext, Plugin, PluginMeta, SettingField
from src.runtime.settings_form import current_setting

log = logging.getLogger(__name__)

JOB = "broadcast"
_HARD_MAX = 300
_pending: dict[int, dict[str, Any]] = {}


def _kind(chat: Any) -> str:
    raw = getattr(chat, "type", None)
    value = getattr(raw, "value", raw)
    return str(value or "").split(".")[-1].lower()


def wants_chat(chat: Any, scope: str) -> bool:
    kind = _kind(chat)
    if kind == "private":
        return scope == "private"
    if scope == "groups":
        return kind in {"group", "supergroup"}
    return False


class BroadcastPlugin(Plugin):
    meta = PluginMeta(
        name="broadcast",
        description_en="Send one message to your groups or private chats. Asks for confirmation.",
        description_ar="إرسال رسالة إلى مجموعاتك أو خاصك. يطلب تأكيداً قبل الإرسال.",
        commands=(
            *aliases(
                "Prepare a group broadcast. Confirm before it sends.",
                "تجهيز إذاعة للمجموعات. لن تُرسل قبل التأكيد.",
                "اذاعة",
                "broadcast",
            ),
            *aliases(
                "Prepare a private-chat broadcast.",
                "تجهيز إذاعة للخاص.",
                "اذاعة خاص",
                "pbroadcast",
            ),
            *aliases(
                "Send the prepared broadcast.",
                "إرسال الإذاعة المجهزة.",
                "تأكيد الاذاعة",
                "confirmbroadcast",
            ),
            *aliases("Stop the broadcast.", "إيقاف الإذاعة.", "ايقاف الاذاعة", "broadcaststop"),
        ),
        default_enabled=True,
        settings=(
            SettingField("max_targets", "Chat cap", "حد المحادثات", "int", 100, 1, _HARD_MAX),
        ),
    )

    async def handle(self, ctx: CommandContext) -> None:
        if ctx.command in {"ايقاف الاذاعة", "broadcaststop"}:
            if cancel_job(ctx.account_id, JOB):
                await ctx.reply(present(ctx, "Stopping the broadcast.", "سيتم إيقاف الإذاعة."))
            else:
                await ctx.reply(present(ctx, "No broadcast is running.", "لا توجد إذاعة تعمل."))
            return
        if ctx.command in {"تأكيد الاذاعة", "confirmbroadcast"}:
            await _run(ctx)
            return
        scope = "private" if ctx.command in {"اذاعة خاص", "pbroadcast"} else "groups"
        text = (ctx.args or "").strip()
        if not text:
            await ctx.reply(
                present(ctx, "Write the message after the command.", "اكتب الرسالة بعد الأمر.")
            )
            return
        if len(text) > 3500:
            await ctx.reply(present(ctx, "That message is too long.", "هذه الرسالة طويلة جداً."))
            return
        _pending[ctx.account_id] = {"scope": scope, "text": text}
        label = "private chats" if scope == "private" else "groups"
        label_ar = "المحادثات الخاصة" if scope == "private" else "المجموعات"
        await ctx.reply(
            present(
                ctx,
                f"Ready to send to {label}. Nothing was sent. "
                f"Send {ctx.prefix}confirmbroadcast to start, "
                f"or {ctx.prefix}broadcaststop to discard.",
                f"جاهز للإرسال إلى {label_ar}. لم يُرسل شيء. "
                f"أرسل {ctx.prefix}تأكيد الاذاعة للبدء، أو {ctx.prefix}ايقاف الاذاعة للإلغاء.",
            )
        )


async def _run(ctx: CommandContext) -> None:
    pending = _pending.get(ctx.account_id)
    if not pending:
        await ctx.reply(
            present(
                ctx,
                "Nothing is waiting. Prepare a broadcast first.",
                "لا شيء بانتظار الإرسال. جهّز الإذاعة أولاً.",
            )
        )
        return
    cancel = begin_job(ctx.account_id, JOB)
    if cancel is None:
        await ctx.reply(present(ctx, "A broadcast is already running.", "الإذاعة تعمل بالفعل."))
        return
    scope = str(pending["scope"])
    text = str(pending["text"])
    _pending.pop(ctx.account_id, None)
    cap = int(current_setting(ctx.account_id, plugin.meta.name, plugin.meta.settings[0]) or 100)
    cap = max(1, min(cap, _HARD_MAX))
    sent = 0
    failed = 0

    async def _collect() -> list[Any]:
        found: list[Any] = []
        async for dialog in ctx.client.get_dialogs():
            if cancel.is_set() or len(found) >= cap:
                break
            chat = getattr(dialog, "chat", dialog)
            if wants_chat(chat, scope) and getattr(chat, "id", None) is not None:
                found.append(chat.id)
        return found

    try:
        targets = await ctx.limiter.run(_collect)
        status = await ctx.reply(present(ctx, "Broadcast started.", "بدأت الإذاعة."))
        for chat_id in targets:
            if cancel.is_set():
                break
            try:
                await ctx.limiter.run(ctx.client.send_message, chat_id, text)
                sent += 1
            except Exception:
                failed += 1
                log.debug("Broadcast send failed", exc_info=True)
            if sent and sent % 5 == 0:
                await _edit_status(ctx, status, sent, failed)
        await ctx.reply(
            present(
                ctx,
                f"Done. Sent {sent}, failed {failed}, cancelled: {cancel.is_set()}. Cap {cap}.",
                f"انتهى. أُرسل {sent}، فشل {failed}، أُلغي: {cancel.is_set()}. الحد {cap}.",
            )
        )
    except Exception:
        log.exception("Broadcast failed for account %s", ctx.account_id)
        await ctx.reply(
            present(
                ctx,
                "The broadcast stopped on an error.",
                "توقفت الإذاعة بسبب خطأ.",
            )
        )
    finally:
        finish_job(ctx.account_id, JOB)


async def _edit_status(ctx: CommandContext, status: Any, sent: int, failed: int) -> None:
    message_id = getattr(status, "id", None)
    chat_id = getattr(getattr(status, "chat", None), "id", None) or getattr(
        getattr(ctx.message, "chat", None), "id", None
    )
    if message_id is None or chat_id is None:
        return
    edit = getattr(ctx.client, "edit_message_text", None)
    if edit is None:
        return
    try:
        await ctx.limiter.run(
            edit,
            chat_id,
            message_id,
            present(
                ctx,
                f"Sent {sent}, failed {failed}.",
                f"أُرسل {sent}، فشل {failed}.",
            ),
        )
    except Exception:
        log.debug("Could not edit broadcast progress", exc_info=True)


plugin = BroadcastPlugin()
