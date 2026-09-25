"""Download video and audio with yt-dlp, off the account event loop."""

from __future__ import annotations

import logging

from src.config import get_settings
from src.plugins.common import aliases, missing_dependency, present, tr
from src.plugins.files import scratch_dir
from src.plugins.proxy_url import account_proxy_url
from src.plugins.slots import cancel_kind, release, try_acquire
from src.plugins.uploads import clear_status, edit_status, send_path
from src.runtime.plugins import CommandContext, Plugin, PluginMeta, SettingField

log = logging.getLogger(__name__)

_VIDEO = ("يوتيوب", "yt")
_AUDIO = ("اغنية", "ytaudio")
_SEARCH = ("بحث", "ytsearch")
_TIKTOK = ("تيك", "tiktok")
_INSTAGRAM = ("انستا", "ig")
_URL = ("تحميل", "dl")
_STOP = ("ايقاف التحميل", "dlstop")


def _limit(ctx: CommandContext, key: str, default: int, low: int, high: int, ceiling: int) -> int:
    raw = ctx.settings.get(key, default)
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = default
    return max(low, min(value, high, ceiling))


class DownloadPlugin(Plugin):
    meta = PluginMeta(
        name="download",
        description_en="Download YouTube, TikTok, Instagram, and other yt-dlp sites.",
        description_ar="تنزيل من يوتيوب وتيك توك وانستغرام والمواقع التي يدعمها yt-dlp.",
        commands=(
            *aliases(
                "Download a YouTube video from a link or a search.",
                "تنزيل فيديو يوتيوب من رابط أو بحث.",
                *_VIDEO,
            ),
            *aliases(
                "Download audio from a YouTube link or a search.",
                "تنزيل صوت من رابط يوتيوب أو بحث.",
                *_AUDIO,
            ),
            *aliases(
                "Search YouTube. Nothing is downloaded.",
                "البحث في يوتيوب. لا يتم تنزيل شيء.",
                *_SEARCH,
            ),
            *aliases("Download a TikTok link.", "تنزيل رابط تيك توك.", *_TIKTOK),
            *aliases("Download an Instagram link.", "تنزيل رابط انستغرام.", *_INSTAGRAM),
            *aliases(
                "Download an http(s) link with yt-dlp.",
                "تنزيل رابط http(s) عبر yt-dlp.",
                *_URL,
            ),
            *aliases("Stop the download running on this account.", "إيقاف التنزيل الجاري.", *_STOP),
        ),
        default_enabled=True,
        settings=(
            SettingField(
                "use_proxy",
                "Use the account proxy",
                "استخدام بروكسي الحساب",
                "bool",
                True,
            ),
            SettingField(
                "max_mb",
                "Max file size (MB)",
                "أقصى حجم (ميجابايت)",
                "int",
                50,
                minimum=5,
                maximum=100,
            ),
            SettingField(
                "max_seconds",
                "Max duration (seconds)",
                "أقصى مدة (ثوان)",
                "int",
                600,
                minimum=15,
                maximum=1800,
            ),
            SettingField(
                "slots",
                "Downloads at once for this account",
                "تنزيلات هذا الحساب معاً",
                "int",
                1,
                minimum=1,
                maximum=2,
            ),
        ),
    )

    async def handle(self, ctx: CommandContext) -> None:
        if ctx.command in _STOP:
            count = cancel_kind(ctx.account_id, "download")
            if count:
                await ctx.reply(present(ctx, "Stopping the download.", "جارٍ إيقاف التنزيل."))
            else:
                await ctx.reply(present(ctx, "No download is running.", "لا يوجد تنزيل جارٍ."))
            return
        if ctx.command in _SEARCH:
            await _search(ctx)
            return
        kind = _kind(ctx.command)
        await _download(ctx, kind)


def _kind(command: str) -> str:
    if command in _AUDIO:
        return "audio"
    if command in _TIKTOK:
        return "tiktok"
    if command in _INSTAGRAM:
        return "instagram"
    if command in _URL:
        return "url"
    return "video"


def _limits(ctx: CommandContext) -> tuple[int, int, int]:
    settings = get_settings()
    max_mb = _limit(ctx, "max_mb", settings.download_max_mb, 5, 100, settings.download_max_mb)
    max_seconds = _limit(
        ctx,
        "max_seconds",
        settings.download_max_seconds,
        15,
        1800,
        settings.download_max_seconds,
    )
    slots = _limit(ctx, "slots", 1, 1, 2, 2)
    return max_mb * 1024 * 1024, max_seconds, slots


def _proxy(ctx: CommandContext) -> str | None:
    if not bool(ctx.settings.get("use_proxy", True)):
        return None
    return account_proxy_url(ctx.account_id)


def _explain(ctx: CommandContext, code: str) -> str:
    messages = {
        "empty": ("Send a link or a search.", "أرسل رابطاً أو عبارة بحث."),
        "url": ("Send an http or https link.", "أرسل رابط http أو https."),
        "host": ("That link is for a different command.", "هذا الرابط لأمر آخر."),
        "use_video": ("Use the video command for a link.", "استخدم أمر الفيديو للروابط."),
        "long": ("That text is too long.", "النص طويل."),
        "duration": ("That media is longer than the limit.", "المدة أطول من الحد."),
        "size": ("That file is larger than the limit.", "الملف أكبر من الحد."),
        "cancelled": ("Download cancelled.", "أُلغي التنزيل."),
        "busy": ("A download is already running.", "هناك تنزيل جارٍ بالفعل."),
        "missing": missing_dependency(ctx, "yt-dlp"),
        "failed": ("Download failed.", "فشل التنزيل."),
    }
    value = messages.get(code, messages["failed"])
    if isinstance(value, str):
        return value
    return present(ctx, value[0], value[1])


async def _search(ctx: CommandContext) -> None:
    from src.media_jobs import classify_target
    from src.runtime.work_pool import run_named

    target = classify_target("search", ctx.args or "")
    if not target.get("ok"):
        await ctx.reply(_explain(ctx, str(target.get("error"))))
        return
    _bytes, _seconds, slots = _limits(ctx)
    slot = try_acquire(ctx.account_id, "download", slots)
    if slot is None:
        await ctx.reply(_explain(ctx, "busy"))
        return
    status = await ctx.reply(present(ctx, "Searching…", "جارٍ البحث…"))
    try:
        result = await run_named(
            ctx.account_id,
            "search",
            {"query": target["query"], "limit": 5, "proxy": _proxy(ctx)},
            slots=slots,
            cancel=slot.cancel,
        )
    finally:
        release(ctx.account_id, "download", slot)
    if not result.get("ok"):
        code = str(result.get("error") or "failed")
        if code == "missing":
            await edit_status(
                ctx, status, missing_dependency(ctx, str(result.get("detail") or "yt-dlp"))
            )
        else:
            await edit_status(ctx, status, _explain(ctx, code))
        return
    rows = result.get("results") or []
    if not rows:
        await edit_status(ctx, status, present(ctx, "Nothing was found.", "لم يُعثر على شيء."))
        return
    lines = []
    for index, row in enumerate(rows, start=1):
        duration = row.get("duration")
        clock = ""
        if isinstance(duration, int) and duration > 0:
            clock = f" ({duration // 60}:{duration % 60:02d})"
        lines.append(f"{index}. {row.get('title') or '—'}{clock}\n{row.get('url') or ''}")
    await edit_status(ctx, status, "\n".join(lines)[:3900])


async def _download(ctx: CommandContext, kind: str) -> None:
    from pathlib import Path

    from src.media_jobs import classify_target
    from src.runtime.work_pool import run_named

    target = classify_target(kind, ctx.args or "")
    if not target.get("ok"):
        await ctx.reply(_explain(ctx, str(target.get("error"))))
        return
    max_bytes, max_seconds, slots = _limits(ctx)
    slot = try_acquire(ctx.account_id, "download", slots)
    if slot is None:
        await ctx.reply(_explain(ctx, "busy"))
        return
    status = await ctx.reply(present(ctx, "Downloading…", "جارٍ التنزيل…"))
    try:
        with scratch_dir() as root:
            result = await run_named(
                ctx.account_id,
                "download",
                {
                    "directory": str(root),
                    "url": target["url"],
                    "audio": bool(target.get("audio")),
                    "max_bytes": max_bytes,
                    "max_seconds": max_seconds,
                    "proxy": _proxy(ctx),
                },
                slots=slots,
                cancel=slot.cancel,
            )
            if not result.get("ok"):
                code = str(result.get("error") or "failed")
                detail = str(result.get("detail") or "")
                log.info("download failed account=%s error=%s", ctx.account_id, code)
                if code == "missing":
                    text = missing_dependency(ctx, detail or "yt-dlp")
                else:
                    text = _explain(ctx, code)
                await edit_status(ctx, status, text)
                return
            path = Path(str(result.get("path") or ""))
            if not path.is_file():
                await edit_status(ctx, status, _explain(ctx, "empty"))
                return
            caption = str(result.get("title") or "")
            if result.get("audio") and result.get("kind") == "document":
                note = tr(
                    ctx,
                    "Sent as a file because ffmpeg is not installed.",
                    "أُرسل كملف لأن ffmpeg غير مثبت.",
                )
                caption = f"{caption}\n{note}".strip()
            await edit_status(ctx, status, present(ctx, "Uploading…", "جارٍ الرفع…"))
            await send_path(
                ctx,
                path,
                kind=str(result.get("kind") or "document"),
                caption=caption,
                status=status,
            )
        await clear_status(ctx, status)
    finally:
        release(ctx.account_id, "download", slot)


plugin = DownloadPlugin()
