"""Send a local file through the account limiter, editing a status message."""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

from src.plugins.common import chat_id_of
from src.runtime.plugins import CommandContext

log = logging.getLogger(__name__)

_PARAM = {
    "video": "video",
    "audio": "audio",
    "voice": "voice",
    "animation": "animation",
    "photo": "photo",
    "document": "document",
    "sticker": "sticker",
}
_METHOD = {kind: f"send_{kind}" for kind in _PARAM}


def _chat(ctx: CommandContext) -> int | str:
    return chat_id_of(ctx.message) or "me"


async def edit_status(ctx: CommandContext, status: Any, text: str) -> None:
    message_id = getattr(status, "id", None)
    if message_id is None:
        return

    async def _edit() -> None:
        await ctx.client.edit_message_text(_chat(ctx), message_id, text)

    try:
        await ctx.limiter.run(_edit)
    except Exception:
        log.debug("Could not edit a status message", exc_info=True)


async def clear_status(ctx: CommandContext, status: Any) -> None:
    message_id = getattr(status, "id", None)
    if message_id is None:
        return

    async def _delete() -> None:
        await ctx.client.delete_messages(_chat(ctx), message_id)

    try:
        await ctx.limiter.run(_delete)
    except Exception:
        log.debug("Could not delete a status message", exc_info=True)


async def send_path(
    ctx: CommandContext,
    path: Path,
    *,
    kind: str,
    caption: str,
    status: Any,
) -> None:
    """Upload ``path``. Progress edits bypass the limiter lock held by the send."""
    method_name = _METHOD.get(kind, "send_document")
    param = _PARAM.get(kind, "document")
    if not hasattr(ctx.client, method_name):
        method_name = "send_document"
        param = "document"
    method = getattr(ctx.client, method_name)
    chat_id = _chat(ctx)
    reply_id = getattr(ctx.message, "id", None)
    status_id = getattr(status, "id", None)
    last = {"at": 0.0, "pct": -1}

    async def progress(current: int, total: int) -> None:
        if status_id is None or total <= 0:
            return
        now = time.monotonic()
        pct = min(100, int(current * 100 / total))
        if pct != 100 and now - last["at"] < 4 and pct - last["pct"] < 15:
            return
        last["at"] = now
        last["pct"] = pct
        label = "جارٍ الرفع" if ctx.language == "ar" else "Uploading"
        try:
            await ctx.client.edit_message_text(chat_id, status_id, f"{label} {pct}%")
        except Exception:
            log.debug("Progress edit failed", exc_info=True)

    async def _send() -> Any:
        from pyrogram import enums

        extra: dict[str, Any] = {"progress": progress}
        if reply_id is not None:
            extra["reply_to_message_id"] = reply_id
        if caption and param != "sticker":
            extra["caption"] = caption[:1024]
            extra["parse_mode"] = enums.ParseMode.DISABLED
        return await method(chat_id, **{param: str(path)}, **extra)

    await ctx.limiter.run(_send)
