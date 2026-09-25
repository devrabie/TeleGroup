"""Upload text or a replied image to telegra.ph."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from src.plugins.common import aliases, message_text, present
from src.plugins.files import download_reply, message_file_size, scratch_dir
from src.runtime.plugins import CommandContext, Plugin, PluginMeta

log = logging.getLogger(__name__)

_CMD = ("تليجراف", "telegraph")
_MAX_IMAGE = 5 * 1024 * 1024
_MAX_TEXT = 8000


class TelegraphPlugin(Plugin):
    meta = PluginMeta(
        name="telegraph",
        description_en="Upload text or a replied image to telegra.ph.",
        description_ar="رفع نص أو صورة بالرد إلى telegra.ph.",
        commands=(
            *aliases(
                "Upload text or a replied image to telegra.ph.",
                "رفع نص أو صورة بالرد إلى telegra.ph.",
                *_CMD,
            ),
        ),
        default_enabled=True,
    )

    async def handle(self, ctx: CommandContext) -> None:
        reply = getattr(ctx.message, "reply_to_message", None)
        text = (ctx.args or "").strip()
        if not text and reply is not None:
            text = message_text(reply).strip()
        image = reply is not None and (
            getattr(reply, "photo", None) is not None
            or str(getattr(getattr(reply, "document", None), "mime_type", "")).startswith("image/")
        )
        if image:
            await _image(ctx, reply)
            return
        if not text:
            await ctx.reply(
                present(
                    ctx,
                    "Send text or reply to an image.",
                    "أرسل نصاً أو رد على صورة.",
                )
            )
            return
        token = str(ctx.settings.get("token", "") or "")
        try:
            token, url = await publish_text(text[:_MAX_TEXT], token)
        except Exception:
            log.info("telegraph text failed account=%s", ctx.account_id)
            await ctx.reply(present(ctx, "Telegraph upload failed.", "فشل الرفع إلى تيليغراف."))
            return
        if token:
            ctx.settings.set("token", token)
        if not url:
            await ctx.reply(present(ctx, "Telegraph upload failed.", "فشل الرفع إلى تيليغراف."))
            return
        await ctx.reply(url)


async def _image(ctx: CommandContext, reply: object) -> None:
    size = message_file_size(reply)
    if size is not None and size > _MAX_IMAGE:
        await ctx.reply(
            present(
                ctx,
                "Telegraph accepts images up to 5 MB.",
                "تيليغراف يقبل صوراً حتى 5 ميغابايت.",
            )
        )
        return
    with scratch_dir() as root:
        path = await download_reply(ctx, root)
        if path is None:
            await ctx.reply(present(ctx, "Could not download that image.", "تعذر تنزيل الصورة."))
            return
        if path.stat().st_size > _MAX_IMAGE:
            await ctx.reply(
                present(
                    ctx,
                    "Telegraph accepts images up to 5 MB.",
                    "تيليغراف يقبل صوراً حتى 5 ميغابايت.",
                )
            )
            return
        try:
            url = await publish_file(path)
        except Exception:
            log.info("telegraph file failed account=%s", ctx.account_id)
            await ctx.reply(present(ctx, "Telegraph upload failed.", "فشل الرفع إلى تيليغراف."))
            return
    await ctx.reply(url)


async def publish_text(text: str, token: str) -> tuple[str, str]:
    import httpx

    async with httpx.AsyncClient(timeout=20) as client:
        access = token
        if not access:
            created = await client.get(
                "https://api.telegra.ph/createAccount",
                params={"short_name": "TeleGroup", "author_name": "TeleGroup"},
            )
            created.raise_for_status()
            body = created.json()
            if not body.get("ok"):
                raise RuntimeError("account")
            access = str(body["result"]["access_token"])
        content = json.dumps([{"tag": "p", "children": [text]}], ensure_ascii=False)
        page = await client.post(
            "https://api.telegra.ph/createPage",
            data={
                "access_token": access,
                "title": "Note",
                "author_name": "TeleGroup",
                "content": content,
                "return_content": "false",
            },
        )
        page.raise_for_status()
        body = page.json()
    if not body.get("ok"):
        raise RuntimeError("page")
    return access, str(body["result"]["url"])


async def publish_file(path: Path) -> str:
    import httpx

    async with httpx.AsyncClient(timeout=30) as client:
        with path.open("rb") as handle:
            response = await client.post(
                "https://telegra.ph/upload",
                files={"file": (path.name, handle)},
            )
        response.raise_for_status()
        body = response.json()
    if isinstance(body, dict) and body.get("error"):
        raise RuntimeError("upload")
    src = body[0]["src"]
    if not str(src).startswith("/"):
        raise RuntimeError("src")
    return "https://telegra.ph" + str(src)


plugin = TelegraphPlugin()
