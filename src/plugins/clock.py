"""Show the time and date in the configured timezone."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from src.config import get_settings
from src.plugins.common import aliases, tr
from src.runtime.plugins import CommandContext, Plugin, PluginMeta

_TIME = ("الوقت", "time")
_DATE = ("التاريخ", "date")


def zone() -> tuple[ZoneInfo, str]:
    name = get_settings().display_timezone or "UTC"
    try:
        return ZoneInfo(name), name
    except ZoneInfoNotFoundError:
        return ZoneInfo("UTC"), "UTC"


def format_clock(now: datetime, *, date: bool) -> str:
    if date:
        return now.strftime("%Y-%m-%d")
    label = now.tzname() or "UTC"
    return f"{now.strftime('%H:%M:%S')} ({label})"


class ClockPlugin(Plugin):
    meta = PluginMeta(
        name="clock",
        description_en="Show the time or the date in the display timezone.",
        description_ar="عرض الوقت أو التاريخ حسب منطقة العرض.",
        commands=(
            *aliases("Show the current time.", "عرض الوقت الحالي.", *_TIME),
            *aliases("Show today's date.", "عرض تاريخ اليوم.", *_DATE),
        ),
        default_enabled=True,
    )

    async def handle(self, ctx: CommandContext) -> None:
        found, name = zone()
        now = datetime.now(found)
        text = format_clock(now, date=ctx.command in _DATE)
        if name == "UTC" and get_settings().display_timezone not in {"UTC", "utc"}:
            text = tr(
                ctx,
                f"{text}\nUnknown timezone, using UTC.",
                f"{text}\nالمنطقة غير معروفة، استُخدم UTC.",
            )
        await ctx.reply(text)


plugin = ClockPlugin()
