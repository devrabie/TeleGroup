"""Convert replied media with ffmpeg: gif, voice note, or mp3."""

from __future__ import annotations

from pathlib import Path

from src.config import get_settings
from src.media_jobs import GIF_MAX_SECONDS
from src.plugins.common import aliases, missing_dependency, tr
from src.plugins.files import download_reply, message_file_size, scratch_dir
from src.plugins.uploads import clear_status, edit_status, send_path
from src.runtime.plugins import CommandContext, Plugin, PluginMeta

_GIF = ("لمتحرك", "gif")
_VOICE = ("بصمة", "voice")
_MP3 = ("لمقطع", "tomp3")


class ConvertPlugin(Plugin):
    meta = PluginMeta(
        name="convert",
        description_en="Turn replied video or audio into a gif, voice note, or mp3.",
        description_ar="تحويل فيديو أو صوت بالرد إلى متحركة أو بصمة أو mp3.",
        commands=(
            *aliases(
                "Turn a replied video into a short gif.",
                "تحويل فيديو بالرد إلى متحركة قصيرة.",
                *_GIF,
            ),
            *aliases(
                "Turn replied media into a voice note.", "تحويل الوسائط بالرد إلى بصمة.", *_VOICE
            ),
            *aliases("Extract mp3 audio from a reply.", "استخراج صوت mp3 من الرد.", *_MP3),
        ),
        default_enabled=True,
    )

    async def handle(self, ctx: CommandContext) -> None:
        from src.runtime.work_pool import run_named

        reply = getattr(ctx.message, "reply_to_message", None)
        if reply is None:
            await ctx.reply(
                tr(ctx, "Reply to a video or an audio file.", "رد على فيديو أو ملف صوت.")
            )
            return
        if ctx.command in _GIF:
            job, dest_name, limit = "gif", "clip.gif", GIF_MAX_SECONDS
        elif ctx.command in _VOICE:
            job, dest_name, limit = (
                "voice",
                "note.ogg",
                min(120, get_settings().download_max_seconds),
            )
        else:
            job, dest_name, limit = (
                "mp3",
                "audio.mp3",
                min(600, get_settings().download_max_seconds),
            )
        max_bytes = get_settings().download_max_mb * 1024 * 1024
        size = message_file_size(reply)
        if size is not None and size > max_bytes:
            await ctx.reply(tr(ctx, "That file is larger than the limit.", "الملف أكبر من الحد."))
            return
        status = await ctx.reply(tr(ctx, "Converting…", "جارٍ التحويل…"))
        with scratch_dir() as root:
            source = await download_reply(ctx, root)
            if source is None:
                await edit_status(
                    ctx, status, tr(ctx, "Could not download that file.", "تعذر تنزيل الملف.")
                )
                return
            if source.stat().st_size > max_bytes:
                await edit_status(
                    ctx,
                    status,
                    tr(ctx, "That file is larger than the limit.", "الملف أكبر من الحد."),
                )
                return
            dest = root / dest_name
            result = await run_named(
                ctx.account_id,
                "convert",
                {
                    "kind": job,
                    "src": str(source),
                    "dest": str(dest),
                    "max_seconds": limit,
                    "max_bytes": max_bytes,
                },
                slots=1,
            )
            if not result.get("ok"):
                if result.get("error") == "missing":
                    text = missing_dependency(ctx, "ffmpeg")
                elif result.get("error") == "size":
                    text = tr(ctx, "The result is larger than the limit.", "الناتج أكبر من الحد.")
                else:
                    text = tr(ctx, "Could not convert that file.", "تعذر تحويل الملف.")
                await edit_status(ctx, status, text)
                return
            await edit_status(ctx, status, tr(ctx, "Uploading…", "جارٍ الرفع…"))
            await send_path(
                ctx,
                Path(str(result["path"])),
                kind=str(result.get("kind") or "document"),
                caption="",
                status=status,
            )
        await clear_status(ctx, status)


plugin = ConvertPlugin()
