"""Repeat a short message a few times, with a hard cap."""

from __future__ import annotations

import asyncio

from src.plugins.common import aliases, message_text, present
from src.plugins.slots import cancel_kind, release, try_acquire
from src.runtime.plugins import CommandContext, Plugin, PluginMeta, SettingField

_CMD = ("تكرار", "repeat")
_STOP = ("ايقاف التكرار", "repeatstop")
HARD_CAP = 8
_MAX_CHARS = 300
_ARABIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")


def parse_count(token: str) -> int | None:
    cleaned = token.translate(_ARABIC_DIGITS)
    if cleaned.isdigit():
        return int(cleaned)
    return None


def parse_repeat(args: str, reply: str, *, cap: int) -> tuple[int, str, str]:
    """Return ``(count, text, error)``. ``error`` is empty when the request is safe."""
    limit = max(1, min(int(cap), HARD_CAP))
    tokens = (args or "").strip().split(maxsplit=1)
    if not tokens:
        return 0, "", "missing"
    count = parse_count(tokens[0])
    if count is None:
        return 0, "", "count"
    text = tokens[1].strip() if len(tokens) > 1 else (reply or "").strip()
    if count < 1:
        return 0, "", "count"
    if count > limit:
        return count, text, "cap"
    if not text:
        return count, "", "text"
    if len(text) > _MAX_CHARS:
        return count, text, "long"
    return count, text, ""


class RepeatPlugin(Plugin):
    meta = PluginMeta(
        name="repeat",
        description_en="Repeat a short message a few times. The hard cap is 8.",
        description_ar="تكرار رسالة قصيرة بعدد محدود. السقف 8.",
        commands=(
            *aliases(
                "Repeat text. Usage: count, then the text. Reply to use that message.",
                "تكرار نص. الاستخدام: العدد ثم النص. الرد يستخدم تلك الرسالة.",
                *_CMD,
            ),
            *aliases("Stop a repeat that is still sending.", "إيقاف تكرار لم ينتهِ.", *_STOP),
        ),
        default_enabled=True,
        settings=(
            SettingField(
                "max_count", "Maximum repeats", "أقصى تكرار", "int", 5, minimum=1, maximum=8
            ),
            SettingField(
                "delay",
                "Seconds between messages",
                "الثواني بين الرسائل",
                "int",
                2,
                minimum=1,
                maximum=10,
            ),
        ),
    )

    async def handle(self, ctx: CommandContext) -> None:
        if ctx.command in _STOP:
            if cancel_kind(ctx.account_id, "repeat"):
                await ctx.reply(present(ctx, "Stopping.", "جارٍ الإيقاف."))
            else:
                await ctx.reply(present(ctx, "Nothing is repeating.", "لا يوجد تكرار جارٍ."))
            return
        raw_cap = ctx.settings.get("max_count", 5)
        try:
            cap = int(raw_cap)
        except (TypeError, ValueError):
            cap = 5
        cap = max(1, min(cap, HARD_CAP))
        reply = getattr(ctx.message, "reply_to_message", None)
        count, text, error = parse_repeat(
            ctx.args or "",
            message_text(reply) if reply else "",
            cap=cap,
        )
        if error == "cap":
            await ctx.reply(present(ctx, f"The maximum is {cap}.", f"الحد الأقصى {cap}."))
            return
        if error:
            await ctx.reply(
                present(
                    ctx,
                    "Use a count from 1 to the limit, then short text.",
                    "أرسل عدداً ضمن الحد ثم نصاً قصيراً.",
                )
            )
            return
        if text.startswith(ctx.prefix):
            await ctx.reply(present(ctx, "Commands are not repeated.", "لا يُكرر أمر."))
            return
        slot = try_acquire(ctx.account_id, "repeat", 1)
        if slot is None:
            await ctx.reply(present(ctx, "A repeat is already running.", "هناك تكرار جارٍ."))
            return
        delay = ctx.settings.get("delay", 2)
        try:
            delay_seconds = max(1, min(int(delay), 10))
        except (TypeError, ValueError):
            delay_seconds = 2
        chat = getattr(getattr(ctx.message, "chat", None), "id", None) or "me"
        try:
            for index in range(count):
                if slot.cancel.is_set():
                    break
                await ctx.limiter.run(lambda: ctx.client.send_message(chat, text))
                if index + 1 < count and not slot.cancel.is_set():
                    await asyncio.sleep(delay_seconds)
        finally:
            release(ctx.account_id, "repeat", slot)


plugin = RepeatPlugin()
