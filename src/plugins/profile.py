"""Set this account's name, bio, or profile photo."""

from __future__ import annotations

from src.plugins.common import aliases, message_text, present
from src.plugins.files import download_reply, scratch_dir
from src.runtime.plugins import CommandContext, Plugin, PluginMeta

_NAME = ("وضع الاسم", "setname")
_BIO = ("وضع البايو", "setbio")
_PHOTO = ("وضع الصورة", "setphoto")
_BIO_MAX = 70


def split_name(text: str) -> tuple[str, str] | None:
    cleaned = " ".join(text.split())
    if not cleaned:
        return None
    first, _, last = cleaned.partition(" ")
    return first[:64], last[:64]


class ProfilePlugin(Plugin):
    meta = PluginMeta(
        name="profile",
        description_en="Set this account's name, bio, or profile photo.",
        description_ar="تغيير اسم هذا الحساب أو نبذته أو صورته.",
        commands=(
            *aliases(
                "Set the first name, and the rest as the last name.",
                "تعيين الاسم الأول، والباقي اسماً أخيراً.",
                *_NAME,
            ),
            *aliases(
                "Set the bio from text or a reply. 70 characters.",
                "تعيين النبذة من نص أو رد. 70 حرفاً.",
                *_BIO,
            ),
            *aliases(
                "Set the profile photo from a replied image.",
                "تعيين صورة الحساب من صورة بالرد.",
                *_PHOTO,
            ),
        ),
        default_enabled=True,
    )

    async def handle(self, ctx: CommandContext) -> None:
        if ctx.command in _NAME:
            await _name(ctx)
            return
        if ctx.command in _BIO:
            await _bio(ctx)
            return
        await _photo(ctx)


async def _name(ctx: CommandContext) -> None:
    parsed = split_name(ctx.args or "")
    if parsed is None:
        await ctx.reply(present(ctx, "Send the new name.", "أرسل الاسم الجديد."))
        return
    first, last = parsed

    async def _update() -> bool:
        return await ctx.client.update_profile(first_name=first, last_name=last)

    await ctx.limiter.run(_update)
    await ctx.reply(present(ctx, "Name updated.", "تم تحديث الاسم."))


async def _bio(ctx: CommandContext) -> None:
    reply = getattr(ctx.message, "reply_to_message", None)
    text = (ctx.args or "").strip() or (message_text(reply) if reply else "")
    text = text.strip()
    if not text:
        await ctx.reply(present(ctx, "Send the new bio.", "أرسل النبذة الجديدة."))
        return
    if len(text) > _BIO_MAX:
        await ctx.reply(present(ctx, "The bio limit is 70 characters.", "حد النبذة 70 حرفاً."))
        return

    async def _update() -> bool:
        return await ctx.client.update_profile(bio=text)

    await ctx.limiter.run(_update)
    await ctx.reply(present(ctx, "Bio updated.", "تم تحديث النبذة."))


async def _photo(ctx: CommandContext) -> None:
    reply = getattr(ctx.message, "reply_to_message", None)
    photo = getattr(reply, "photo", None)
    document = getattr(reply, "document", None)
    mime = str(getattr(document, "mime_type", "") or "")
    if reply is None or (photo is None and not mime.startswith("image/")):
        await ctx.reply(present(ctx, "Reply to an image.", "رد على صورة."))
        return
    with scratch_dir() as root:
        path = await download_reply(ctx, root)
        if path is None:
            await ctx.reply(present(ctx, "Could not download that image.", "تعذر تنزيل الصورة."))
            return

        async def _update() -> bool:
            return await ctx.client.set_profile_photo(photo=str(path))

        await ctx.limiter.run(_update)
    await ctx.reply(present(ctx, "Profile photo updated.", "تم تحديث صورة الحساب."))


plugin = ProfilePlugin()
