"""Latency check for the managed account."""

from __future__ import annotations

from datetime import UTC, datetime

from src.plugins.common import aliases
from src.runtime.plugins import CommandContext, Plugin, PluginMeta
from src.templates import card, field


def _latency_ms(message: object) -> float:
    sent_at = getattr(message, "date", None)
    if not isinstance(sent_at, datetime):
        return 0.0
    if sent_at.tzinfo is None:
        sent_at = sent_at.replace(tzinfo=UTC)
    return max(0.0, (datetime.now(UTC) - sent_at).total_seconds() * 1000)


class PingPlugin(Plugin):
    meta = PluginMeta(
        name="ping",
        description_en="Reply with this account's latency.",
        description_ar="الرد بزمن استجابة هذا الحساب.",
        commands=(
            *aliases(
                "Reply with this account's latency.",
                "الرد بزمن استجابة هذا الحساب.",
                "فحص",
                "ping",
            ),
        ),
        default_enabled=True,
    )

    async def handle(self, ctx: CommandContext) -> None:
        latency = _latency_ms(ctx.message)
        if ctx.language == "ar":
            title = "فحص"
            label = "الاستجابة"
            unit = "مللي ثانية"
        else:
            title = "Ping"
            label = "Latency"
            unit = "ms"
        await ctx.reply(card(ctx.language, f"🏓 {title}", field(label, f"{latency:.0f} {unit}")))


plugin = PingPlugin()
