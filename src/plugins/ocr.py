"""Read text from a replied image. Requires the optional tesseract program."""

from __future__ import annotations

from src.plugins.common import aliases, missing_dependency, tr
from src.plugins.files import download_reply, message_file_size, scratch_dir
from src.plugins.uploads import edit_status
from src.runtime.plugins import CommandContext, Plugin, PluginMeta, SettingField

_CMD = ("استخراج", "ocr")
_MAX_BYTES = 8 * 1024 * 1024


class OcrPlugin(Plugin):
    meta = PluginMeta(
        name="ocr",
        description_en="Read text from a replied image when tesseract is installed.",
        description_ar="قراءة النص من صورة بالرد عند تثبيت tesseract.",
        commands=(
            *aliases(
                "Read text from a replied image. Optional tesseract language.",
                "قراءة نص من صورة بالرد. يمكن تحديد لغة tesseract.",
                *_CMD,
            ),
        ),
        default_enabled=True,
        settings=(
            SettingField(
                "lang",
                "Tesseract languages",
                "لغات التعرف",
                "str",
                "ara+eng",
            ),
        ),
    )

    async def handle(self, ctx: CommandContext) -> None:
        from src.runtime.work_pool import run_named

        reply = getattr(ctx.message, "reply_to_message", None)
        if reply is None:
            await ctx.reply(tr(ctx, "Reply to an image.", "رد على صورة."))
            return
        size = message_file_size(reply)
        if size is not None and size > _MAX_BYTES:
            await ctx.reply(tr(ctx, "That image is too large.", "الصورة كبيرة جداً."))
            return
        lang = (ctx.args or "").strip() or str(ctx.settings.get("lang", "ara+eng") or "ara+eng")
        status = await ctx.reply(tr(ctx, "Reading…", "جارٍ القراءة…"))
        with scratch_dir() as root:
            source = await download_reply(ctx, root)
            if source is None:
                await edit_status(
                    ctx, status, tr(ctx, "Could not download that image.", "تعذر تنزيل الصورة.")
                )
                return
            if source.stat().st_size > _MAX_BYTES:
                await edit_status(
                    ctx, status, tr(ctx, "That image is too large.", "الصورة كبيرة جداً.")
                )
                return
            result = await run_named(
                ctx.account_id,
                "ocr",
                {"src": str(source), "lang": lang},
                slots=1,
            )
        if not result.get("ok"):
            if result.get("error") == "missing":
                await edit_status(ctx, status, missing_dependency(ctx, "tesseract"))
            elif result.get("error") == "empty":
                await edit_status(ctx, status, tr(ctx, "No text was found.", "لم يُعثر على نص."))
            else:
                await edit_status(
                    ctx, status, tr(ctx, "Could not read that image.", "تعذرت قراءة الصورة.")
                )
            return
        await edit_status(ctx, status, str(result.get("text") or "")[:3900])


plugin = OcrPlugin()
