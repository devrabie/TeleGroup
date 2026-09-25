"""Mention group members in small batches. Cancellable and rate-limited."""

from __future__ import annotations

import logging

from src.plugins.common import (
    aliases,
    chat_id_of,
    html_name,
    is_group_chat,
    present,
    tr,
    user_mention,
)
from src.plugins.jobs import begin_job, cancel_job, finish_job
from src.runtime.plugins import CommandContext, Plugin, PluginMeta, SettingField
from src.runtime.settings_form import current_setting

log = logging.getLogger(__name__)

JOB = "tagall"
_HARD_MAX = 200
_HARD_BATCH = 8


class TagAllPlugin(Plugin):
    meta = PluginMeta(
        name="tagall",
        description_en="Mention members in batches. Stop it with the cancel command.",
        description_ar="إشارة الأعضاء على دفعات. يمكن إيقافها بأمر الإلغاء.",
        commands=(
            *aliases(
                "Mention members. Optional text is sent with each batch.",
                "إشارة الأعضاء. يمكن إرفاق نص مع كل دفعة.",
                "تاك",
                "tagall",
            ),
            *aliases("Stop the mention run.", "إيقاف الإشارة.", "ايقاف التاك", "tagstop"),
        ),
        default_enabled=True,
        settings=(
            SettingField(
                "batch", "Mentions per message", "الإشارات في كل رسالة", "int", 5, 1, _HARD_BATCH
            ),
            SettingField("max_members", "Member cap", "حد الأعضاء", "int", 80, 1, _HARD_MAX),
        ),
    )

    async def handle(self, ctx: CommandContext) -> None:
        if ctx.command in {"ايقاف التاك", "tagstop"}:
            if cancel_job(ctx.account_id, JOB):
                await ctx.reply(present(ctx, "Stopping mentions.", "سيتم إيقاف الإشارة."))
            else:
                await ctx.reply(present(ctx, "No mention run is active.", "لا توجد إشارة تعمل."))
            return
        if not is_group_chat(ctx.message):
            await ctx.reply(present(ctx, "Use this in a group.", "استخدم هذا في مجموعة."))
            return
        chat_id = chat_id_of(ctx.message)
        if not isinstance(chat_id, int):
            await ctx.reply(present(ctx, "This chat has no id.", "هذه المحادثة بلا معرّف."))
            return
        cancel = begin_job(ctx.account_id, JOB)
        if cancel is None:
            await ctx.reply(present(ctx, "A mention run is already going.", "الإشارة تعمل بالفعل."))
            return
        note = (ctx.args or "").strip()
        batch = int(current_setting(ctx.account_id, self.meta.name, self.meta.settings[0]) or 5)
        limit = int(current_setting(ctx.account_id, self.meta.name, self.meta.settings[1]) or 80)
        batch = max(1, min(batch, _HARD_BATCH))
        limit = max(1, min(limit, _HARD_MAX))
        sent = 0
        mentioned = 0

        async def _collect() -> list[str]:
            found: list[str] = []
            async for member in ctx.client.get_chat_members(chat_id):
                if cancel.is_set() or len(found) >= limit:
                    break
                user = getattr(member, "user", None)
                if (
                    user is None
                    or getattr(user, "is_bot", False)
                    or getattr(user, "is_deleted", False)
                ):
                    continue
                if getattr(user, "is_self", False):
                    continue
                user_id = getattr(user, "id", None)
                if user_id is None:
                    continue
                found.append(user_mention(int(user_id), html_name(user)))
            return found

        try:
            mentions = await ctx.limiter.run(_collect)
            mentioned = len(mentions)
            bucket: list[str] = []
            for mention in mentions:
                if cancel.is_set():
                    break
                bucket.append(mention)
                if len(bucket) >= batch:
                    if not await _flush(ctx, chat_id, note, bucket):
                        break
                    sent += 1
                    bucket = []
            if bucket and not cancel.is_set():
                if await _flush(ctx, chat_id, note, bucket):
                    sent += 1
            stopped = tr(ctx, "Stopped.", "تم الإيقاف.") if cancel.is_set() else ""
            await ctx.reply(
                present(
                    ctx,
                    f"Mentioned {mentioned} members in {sent} messages. {stopped}".strip(),
                    f"تمت إشارة {mentioned} عضو في {sent} رسالة. {stopped}".strip(),
                )
            )
        except Exception:
            log.exception("Tag-all failed for account %s", ctx.account_id)
            await ctx.reply(
                present(
                    ctx,
                    "Mentioning stopped because Telegram returned an error.",
                    "توقفت الإشارة بسبب خطأ من تيليجرام.",
                )
            )
        finally:
            finish_job(ctx.account_id, JOB)


async def _flush(ctx: CommandContext, chat_id: int, note: str, mentions: list[str]) -> bool:
    text = " ".join(mentions)
    if note:
        text = f"{note}\n{text}"
    try:
        await ctx.limiter.run(ctx.client.send_message, chat_id, text, parse_mode="html")
    except Exception:
        log.debug("Tag batch failed", exc_info=True)
        return False
    return True


plugin = TagAllPlugin()
