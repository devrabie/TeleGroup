"""Temporary directories that are always removed."""

from __future__ import annotations

import shutil
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from src.runtime.plugins import CommandContext


@contextmanager
def scratch_dir() -> Iterator[Path]:
    root = Path(tempfile.mkdtemp(prefix="tg4-"))
    try:
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)


def inside(root: Path, path: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


async def download_reply(ctx: CommandContext, root: Path) -> Path | None:
    reply = getattr(ctx.message, "reply_to_message", None)
    if reply is None:
        return None
    target = root / "source"

    async def _download() -> str | None:
        return await ctx.client.download_media(reply, file_name=str(target))

    saved = await ctx.limiter.run(_download)
    if not saved:
        return None
    path = Path(str(saved))
    if not path.is_file():
        return None
    if inside(root, path):
        return path
    dest = root / path.name
    shutil.move(str(path), dest)
    return dest


def message_file_size(message: object) -> int | None:
    for name in ("video", "animation", "audio", "voice", "document", "sticker", "video_note"):
        media = getattr(message, name, None)
        size = getattr(media, "file_size", None)
        if size:
            try:
                return int(size)
            except (TypeError, ValueError):
                return None
    photo = getattr(message, "photo", None)
    size = getattr(photo, "file_size", None)
    if size:
        try:
            return int(size)
        except (TypeError, ValueError):
            return None
    return None
