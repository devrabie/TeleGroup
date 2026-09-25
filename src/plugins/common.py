"""Shared helpers for userbot command plugins."""

from __future__ import annotations

import re
from html import escape
from typing import Any

from src.runtime.plugins import BotCommand, CommandContext

_LINK = re.compile(r"(https?://|t\.me/|telegram\.me/|www\.)", re.IGNORECASE)


def tr(ctx: CommandContext, en: str, ar: str) -> str:
    if ctx.language == "ar":
        return ar
    return en


def missing_dependency(ctx: CommandContext, name: str) -> str:
    """Message when a Python package or system program is not installed."""
    labels = {
        "yt-dlp": (
            "yt-dlp is not installed on this server.",
            "حزمة yt-dlp غير مثبتة على الخادم.",
        ),
        "ffmpeg": (
            "ffmpeg is not installed on this server.",
            "ffmpeg غير مثبت على الخادم.",
        ),
        "Pillow": (
            "Pillow is not installed on this server.",
            "حزمة Pillow غير مثبتة على الخادم.",
        ),
        "gTTS": (
            "gTTS is not installed on this server.",
            "حزمة gTTS غير مثبتة على الخادم.",
        ),
        "tesseract": (
            "OCR needs the tesseract program, which is not installed.",
            "التعرف على النص يحتاج برنامج tesseract وهو غير مثبت.",
        ),
    }
    en, ar = labels.get(
        name,
        (f"{name} is not available on this server.", f"{name} غير متاح على الخادم."),
    )
    return tr(ctx, en, ar)


def aliases(description_en: str, description_ar: str, *names: str) -> tuple[BotCommand, ...]:
    return tuple(BotCommand(name, description_en, description_ar) for name in names)


def message_text(message: Any) -> str:
    return str(getattr(message, "text", None) or getattr(message, "caption", None) or "")


def chat_id_of(message: Any) -> int | str | None:
    chat = getattr(message, "chat", None)
    if chat is None:
        return None
    return getattr(chat, "id", None)


def is_group_chat(message: Any) -> bool:
    return chat_kind(message) in {"group", "supergroup", "forum"}


def is_group_or_channel(message: Any) -> bool:
    return is_group_chat(message) or chat_kind(message) == "channel"


def chat_kind(message: Any) -> str:
    chat = getattr(message, "chat", None)
    raw = getattr(chat, "type", None)
    if raw is None:
        return ""
    value = getattr(raw, "value", raw)
    return str(value).split(".")[-1].lower()


def is_saved_chat(message: Any, client: Any) -> bool:
    me = getattr(client, "me", None)
    chat = getattr(message, "chat", None)
    me_id = getattr(me, "id", None)
    chat_id = getattr(chat, "id", None)
    return me_id is not None and chat_id == me_id


def html_name(user: Any) -> str:
    first = getattr(user, "first_name", None) or ""
    last = getattr(user, "last_name", None) or ""
    name = (f"{first} {last}").strip() or getattr(user, "username", None) or "user"
    return escape(str(name))


def user_mention(user_id: int, escaped_name: str) -> str:
    return f'<a href="tg://user?id={user_id}">{escaped_name}</a>'


async def resolve_user(ctx: CommandContext) -> tuple[Any, str]:
    """Return ``(user ref, "")`` or ``(None, error code)``.

    A reply wins. Otherwise the first argument may be ``@username`` or a numeric id.
    The returned ref is passed straight to Kurigram.
    """
    reply = getattr(ctx.message, "reply_to_message", None)
    if reply is not None:
        user = getattr(reply, "from_user", None)
        if user is not None and getattr(user, "id", None) is not None:
            return user.id, ""
        sender = getattr(reply, "sender_chat", None)
        if sender is not None and getattr(sender, "id", None) is not None:
            return sender.id, ""
    token = (ctx.args or "").strip().split()[0] if ctx.args else ""
    if not token:
        return None, "missing"
    if token.startswith("@"):
        if len(token) < 2:
            return None, "missing"
        return token, ""
    number = token[1:] if token.startswith("-") else token
    if number.isdigit():
        return int(token), ""
    return None, "missing"


def has_link(message: Any) -> bool:
    text = message_text(message)
    if _LINK.search(text):
        return True
    entities = list(getattr(message, "entities", None) or [])
    entities.extend(getattr(message, "caption_entities", None) or [])
    for entity in entities:
        kind = str(getattr(entity, "type", "")).lower()
        if "url" in kind or "text_link" in kind:
            return True
    return False


def is_forward(message: Any) -> bool:
    return any(
        getattr(message, name, None)
        for name in ("forward_date", "forward_from", "forward_from_chat", "forward_origin")
    )


def _present(message: Any, name: str) -> bool:
    return getattr(message, name, None) is not None


def lock_matches(message: Any, locks: set[str]) -> bool:
    """True when an incoming message should be removed for these locks."""
    if not locks or getattr(message, "service", False):
        return False
    if "all" in locks:
        return True
    checks = {
        "links": has_link(message),
        "photos": _present(message, "photo"),
        "videos": _present(message, "video"),
        "stickers": _present(message, "sticker"),
        "gifs": _present(message, "animation") or _present(message, "gif"),
        "voice": _present(message, "voice")
        or _present(message, "video_note")
        or _present(message, "audio"),
        "forwards": is_forward(message),
        "media": any(
            _present(message, name)
            for name in (
                "photo",
                "video",
                "audio",
                "document",
                "voice",
                "video_note",
                "animation",
                "sticker",
            )
        ),
        "text": bool(message_text(message))
        and not any(
            _present(message, name)
            for name in (
                "photo",
                "video",
                "audio",
                "document",
                "voice",
                "video_note",
                "animation",
                "sticker",
            )
        ),
    }
    return any(checks.get(name, False) for name in locks)
