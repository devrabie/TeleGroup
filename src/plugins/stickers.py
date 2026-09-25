"""Add stickers to this account's pack, and convert between images and stickers."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from src.media_jobs import STICKER_PACK_LIMIT
from src.plugins.common import aliases, missing_dependency, present
from src.plugins.files import download_reply, message_file_size, scratch_dir
from src.plugins.uploads import send_path
from src.runtime.plugins import CommandContext, Plugin, PluginMeta

log = logging.getLogger(__name__)

_KANG = ("ملصق", "kang")
_INFO = ("معلومات الملصق", "packinfo")
_TO_STICKER = ("لملصق", "tosticker")
_TO_IMAGE = ("لصورة", "toimage")
_MAX_BYTES = 8 * 1024 * 1024


def pack_short_name(user_id: int, index: int) -> str:
    name = f"tg{int(user_id)}p{int(index)}"
    cleaned = "".join(character for character in name if character.isalnum() or character == "_")
    if not cleaned or not cleaned[0].isalpha():
        cleaned = "u" + cleaned
    return cleaned[:64]


def pack_title(index: int) -> str:
    return f"TeleGroup {int(index)}"[:64]


def pack_is_full(count: int) -> bool:
    return int(count) >= STICKER_PACK_LIMIT


def sticker_emoji(args: str, sticker: Any) -> str:
    text = (args or "").strip()
    if text:
        return text.split()[0][:16]
    value = getattr(sticker, "emoji", None)
    if value:
        return str(value)[:16]
    return "🤔"


class StickersPlugin(Plugin):
    meta = PluginMeta(
        name="stickers",
        description_en="Save stickers into this account's pack and convert images.",
        description_ar="حفظ الملصقات في حزمة هذا الحساب وتحويل الصور.",
        commands=(
            *aliases(
                "Add a replied sticker or image to this account's pack.",
                "إضافة ملصق أو صورة بالرد إلى حزمة هذا الحساب.",
                *_KANG,
            ),
            *aliases(
                "Show the pack of a replied sticker.",
                "عرض حزمة الملصق الذي رُد عليه.",
                *_INFO,
            ),
            *aliases(
                "Convert a replied image into a sticker.",
                "تحويل صورة بالرد إلى ملصق.",
                *_TO_STICKER,
            ),
            *aliases(
                "Convert a replied sticker into an image.",
                "تحويل ملصق بالرد إلى صورة.",
                *_TO_IMAGE,
            ),
        ),
        default_enabled=True,
    )

    async def handle(self, ctx: CommandContext) -> None:
        if ctx.command in _INFO:
            await _pack_info(ctx)
            return
        if ctx.command in _TO_IMAGE:
            await _to_image(ctx)
            return
        if ctx.command in _TO_STICKER:
            await _to_sticker(ctx)
            return
        await _kang(ctx)


def _reply(ctx: CommandContext) -> Any:
    return getattr(ctx.message, "reply_to_message", None)


def _static_media(message: Any) -> str:
    """Return ``image``, ``animated``, or ``none``."""
    sticker = getattr(message, "sticker", None)
    if sticker is not None:
        if getattr(sticker, "is_animated", False) or getattr(sticker, "is_video", False):
            return "animated"
        return "image"
    if getattr(message, "photo", None) is not None:
        return "image"
    document = getattr(message, "document", None)
    mime = str(getattr(document, "mime_type", "") or "")
    if mime.startswith("image/"):
        return "image"
    return "none"


async def _prepare_webp(ctx: CommandContext, root: Path) -> dict[str, Any] | None:
    from src.runtime.work_pool import run_named

    reply = _reply(ctx)
    kind = _static_media(reply)
    if kind == "animated":
        await ctx.reply(
            present(
                ctx,
                "Animated and video stickers are not supported.",
                "الملصقات المتحركة وملصقات الفيديو غير مدعومة.",
            )
        )
        return None
    if kind != "image":
        await ctx.reply(present(ctx, "Reply to a sticker or an image.", "رد على ملصق أو صورة."))
        return None
    size = message_file_size(reply)
    if size is not None and size > _MAX_BYTES:
        await ctx.reply(present(ctx, "That file is too large.", "الملف كبير جداً."))
        return None
    source = await download_reply(ctx, root)
    if source is None:
        await ctx.reply(present(ctx, "Could not download that file.", "تعذر تنزيل الملف."))
        return None
    if source.stat().st_size > _MAX_BYTES:
        await ctx.reply(present(ctx, "That file is too large.", "الملف كبير جداً."))
        return None
    dest = root / "sticker.webp"
    result = await run_named(
        ctx.account_id,
        "webp",
        {"src": str(source), "dest": str(dest)},
        slots=1,
    )
    if not result.get("ok"):
        detail = str(result.get("detail") or "Pillow")
        if result.get("error") == "missing":
            await ctx.reply(missing_dependency(ctx, detail))
        else:
            await ctx.reply(present(ctx, "Could not convert that image.", "تعذر تحويل الصورة."))
        return None
    return result


async def _kang(ctx: CommandContext) -> None:
    reply = _reply(ctx)
    if reply is None:
        await ctx.reply(present(ctx, "Reply to a sticker or an image.", "رد على ملصق أو صورة."))
        return
    me = getattr(ctx.client, "me", None)
    user_id = getattr(me, "id", None)
    if user_id is None:
        me = await ctx.limiter.run(ctx.client.get_me)
        user_id = getattr(me, "id", None)
    if user_id is None:
        await ctx.reply(present(ctx, "Could not read this account.", "تعذر قراءة هذا الحساب."))
        return
    owner_id = int(user_id)
    emoji = sticker_emoji(ctx.args or "", getattr(reply, "sticker", None))
    with scratch_dir() as root:
        prepared = await _prepare_webp(ctx, root)
        if prepared is None:
            return
        path = Path(str(prepared["path"]))
        short = await _install(
            ctx,
            path,
            emoji,
            owner_id,
            int(prepared.get("width") or 512),
            int(prepared.get("height") or 512),
        )
        if short is None:
            return
        status = await ctx.reply(present(ctx, "Uploading…", "جارٍ الرفع…"))
        await send_path(ctx, path, kind="sticker", caption="", status=status)
    await ctx.reply(f"https://t.me/addstickers/{short}")


async def _install(
    ctx: CommandContext,
    path: Path,
    emoji: str,
    user_id: int,
    width: int,
    height: int,
) -> str | None:
    index = ctx.settings.get("pack_index", 1)
    try:
        index = max(1, int(index))
    except (TypeError, ValueError):
        index = 1
    for _ in range(6):
        short = pack_short_name(user_id, index)
        try:
            await _add_or_create(ctx, path, emoji, short, pack_title(index), width, height)
        except _Rotate:
            index += 1
            continue
        except Exception:
            log.exception("Sticker pack failed for account %s", ctx.account_id)
            await ctx.reply(
                present(ctx, "Could not update the sticker pack.", "تعذر تحديث حزمة الملصقات.")
            )
            return None
        ctx.settings.set("pack_index", index)
        ctx.settings.set("pack", short)
        return short
    await ctx.reply(present(ctx, "Could not open a new sticker pack.", "تعذر إنشاء حزمة جديدة."))
    return None


class _Rotate(Exception):
    pass


async def _add_or_create(
    ctx: CommandContext,
    path: Path,
    emoji: str,
    short: str,
    title: str,
    width: int,
    height: int,
) -> None:
    from pyrogram import raw
    from pyrogram.errors import PackShortNameOccupied, StickersetInvalid, StickersTooMuch

    document = await _input_document(ctx, path, emoji, width, height)
    item = raw.types.InputStickerSetItem(document=document, emoji=emoji)
    stickerset = raw.types.InputStickerSetShortName(short_name=short)
    try:
        found = await ctx.limiter.run(
            lambda: ctx.client.invoke(
                raw.functions.messages.GetStickerSet(stickerset=stickerset, hash=0)
            )
        )
    except StickersetInvalid:
        found = None
    if found is not None and pack_is_full(int(getattr(found.set, "count", 0) or 0)):
        raise _Rotate
    try:
        if found is None:
            await ctx.limiter.run(
                lambda: ctx.client.invoke(
                    raw.functions.stickers.CreateStickerSet(
                        user_id=raw.types.InputUserSelf(),
                        title=title,
                        short_name=short,
                        stickers=[item],
                        software="TeleGroup",
                    )
                )
            )
        else:
            await ctx.limiter.run(
                lambda: ctx.client.invoke(
                    raw.functions.stickers.AddStickerToSet(stickerset=stickerset, sticker=item)
                )
            )
    except (StickersTooMuch, PackShortNameOccupied) as exc:
        raise _Rotate from exc


async def _input_document(
    ctx: CommandContext, path: Path, emoji: str, width: int, height: int
) -> Any:
    from pyrogram import raw

    async def _upload() -> Any:
        uploaded = await ctx.client.save_file(str(path))
        media = raw.types.InputMediaUploadedDocument(
            mime_type="image/webp",
            file=uploaded,
            attributes=[
                raw.types.DocumentAttributeFilename(file_name="sticker.webp"),
                raw.types.DocumentAttributeImageSize(w=width, h=height),
                raw.types.DocumentAttributeSticker(
                    alt=emoji,
                    stickerset=raw.types.InputStickerSetEmpty(),
                ),
            ],
        )
        peer = await ctx.client.resolve_peer("me")
        result = await ctx.client.invoke(raw.functions.messages.UploadMedia(peer=peer, media=media))
        document = getattr(result, "document", None)
        if document is None:
            raise RuntimeError("sticker upload")
        return raw.types.InputDocument(
            id=document.id,
            access_hash=document.access_hash,
            file_reference=document.file_reference,
        )

    return await ctx.limiter.run(_upload)


async def _to_sticker(ctx: CommandContext) -> None:
    if _reply(ctx) is None:
        await ctx.reply(present(ctx, "Reply to an image.", "رد على صورة."))
        return
    with scratch_dir() as root:
        prepared = await _prepare_webp(ctx, root)
        if prepared is None:
            return
        path = Path(str(prepared["path"]))
        status = await ctx.reply(present(ctx, "Uploading…", "جارٍ الرفع…"))
        await send_path(ctx, path, kind="sticker", caption="", status=status)


async def _to_image(ctx: CommandContext) -> None:
    from src.runtime.work_pool import run_named

    reply = _reply(ctx)
    if reply is None or getattr(reply, "sticker", None) is None:
        await ctx.reply(present(ctx, "Reply to a sticker.", "رد على ملصق."))
        return
    sticker = reply.sticker
    if getattr(sticker, "is_animated", False):
        await ctx.reply(
            present(
                ctx,
                "Animated stickers cannot be converted.",
                "لا يمكن تحويل الملصقات المتحركة.",
            )
        )
        return
    with scratch_dir() as root:
        source = await download_reply(ctx, root)
        if source is None:
            await ctx.reply(present(ctx, "Could not download that sticker.", "تعذر تنزيل الملصق."))
            return
        dest = root / "image.jpg"
        job = "frame" if getattr(sticker, "is_video", False) else "jpeg"
        result = await run_named(
            ctx.account_id,
            job,
            {"src": str(source), "dest": str(dest)},
            slots=1,
        )
        if not result.get("ok"):
            if result.get("error") == "missing":
                await ctx.reply(missing_dependency(ctx, str(result.get("detail") or "Pillow")))
            else:
                await ctx.reply(
                    present(
                        ctx,
                        "Could not convert that sticker.",
                        "تعذر تحويل الملصق.",
                    )
                )
            return
        status = await ctx.reply(present(ctx, "Uploading…", "جارٍ الرفع…"))
        await send_path(ctx, Path(str(result["path"])), kind="photo", caption="", status=status)


def format_pack(found: Any, language: str) -> str:
    sticker_set = getattr(found, "set", found)
    title = getattr(sticker_set, "title", "") or ""
    short = getattr(sticker_set, "short_name", "") or ""
    count = getattr(sticker_set, "count", 0)
    link = f"https://t.me/addstickers/{short}" if short else ""
    from src.templates import card, field

    if language == "ar":
        body = "\n".join([field("الاسم", short), field("العدد", count), link])
        return card(language, title or "الملصق", body).strip()
    body = "\n".join([field("Name", short), field("Count", count), link])
    return card(language, title or "Pack", body).strip()


async def _pack_info(ctx: CommandContext) -> None:
    from pyrogram import raw
    from pyrogram.errors import StickersetInvalid

    reply = _reply(ctx)
    sticker = getattr(reply, "sticker", None)
    short = getattr(sticker, "set_name", None)
    if not short:
        await ctx.reply(
            present(ctx, "Reply to a sticker that belongs to a pack.", "رد على ملصق داخل حزمة.")
        )
        return
    try:
        found = await ctx.limiter.run(
            lambda: ctx.client.invoke(
                raw.functions.messages.GetStickerSet(
                    stickerset=raw.types.InputStickerSetShortName(short_name=str(short)),
                    hash=0,
                )
            )
        )
    except StickersetInvalid:
        await ctx.reply(present(ctx, "That pack is not available.", "هذه الحزمة غير متاحة."))
        return
    await ctx.reply(format_pack(found, ctx.language))


plugin = StickersPlugin()
