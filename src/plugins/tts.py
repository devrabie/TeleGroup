"""Speak text with gTTS. Voice notes need ffmpeg; otherwise an audio file is sent."""

from __future__ import annotations

import re

from src.media_jobs import TTS_MAX_CHARS
from src.plugins.common import aliases, message_text, missing_dependency, present
from src.plugins.files import scratch_dir
from src.plugins.uploads import clear_status, edit_status, send_path
from src.runtime.plugins import CommandContext, Plugin, PluginMeta, SettingField

_CMD = ("نطق", "tts")
_LANG = re.compile(r"^[a-z]{2}(-[A-Za-z]{2})?$")


def split_tts(args: str, reply: str, default_lang: str) -> tuple[str, str] | None:
    tokens = (args or "").strip().split(maxsplit=1)
    lang = (default_lang or "ar").lower()
    text = ""
    if tokens and _LANG.fullmatch(tokens[0].lower()):
        lang = tokens[0].lower()
        text = tokens[1].strip() if len(tokens) > 1 else ""
    else:
        text = (args or "").strip()
    if not text:
        text = (reply or "").strip()
    if not text:
        return None
    return lang, text[:TTS_MAX_CHARS]


class SpeechPlugin(Plugin):
    meta = PluginMeta(
        name="tts",
        description_en="Speak text with gTTS. ffmpeg turns it into a voice note.",
        description_ar="نطق النص عبر gTTS. ffmpeg يحوّله إلى رسالة صوتية.",
        commands=(
            *aliases(
                "Speak text or a reply. Optional language code first.",
                "نطق نص أو رد. يمكن وضع رمز اللغة أولاً.",
                *_CMD,
            ),
        ),
        default_enabled=True,
        settings=(SettingField("lang", "Voice language", "لغة النطق", "str", "ar"),),
    )

    async def handle(self, ctx: CommandContext) -> None:
        from pathlib import Path

        from src.runtime.work_pool import run_named

        reply = getattr(ctx.message, "reply_to_message", None)
        default = str(ctx.settings.get("lang", "ar") or "ar")
        parsed = split_tts(ctx.args or "", message_text(reply) if reply else "", default)
        if parsed is None:
            await ctx.reply(
                present(ctx, "Send text to speak, or reply to it.", "أرسل نصاً لنطقه أو رد عليه.")
            )
            return
        lang, text = parsed
        status = await ctx.reply(present(ctx, "Speaking…", "جارٍ النطق…"))
        with scratch_dir() as root:
            result = await run_named(
                ctx.account_id,
                "tts",
                {"directory": str(root), "text": text, "lang": lang},
                slots=1,
            )
            if not result.get("ok"):
                if result.get("error") == "missing":
                    await edit_status(
                        ctx, status, missing_dependency(ctx, str(result.get("detail") or "gTTS"))
                    )
                elif result.get("error") == "lang":
                    await edit_status(
                        ctx,
                        status,
                        present(
                            ctx,
                            "That language code is not supported.",
                            "رمز اللغة غير مدعوم.",
                        ),
                    )
                else:
                    await edit_status(
                        ctx, status, present(ctx, "Could not speak that text.", "تعذر نطق النص.")
                    )
                return
            path = Path(str(result["path"]))
            await edit_status(ctx, status, present(ctx, "Uploading…", "جارٍ الرفع…"))
            await send_path(
                ctx, path, kind=str(result.get("kind") or "audio"), caption="", status=status
            )
        await clear_status(ctx, status)


plugin = SpeechPlugin()
