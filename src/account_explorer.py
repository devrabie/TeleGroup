"""Fetch and format a managed account's Telegram profile, gifts, and private chats."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from html import escape
from typing import Any, Callable, Optional

from pyrogram.enums import ChatType, MessageMediaType
import asyncio
from pyrogram.errors import FloodWait, Timeout, RPCError

from src.cache_store import cache_store
from src.code_monitor import AUTH_ERRORS
from src.database import mark_session_invalid, mark_session_ok, session_is_invalid
from src.two_step import TwoStepError, open_account_client

log = logging.getLogger(__name__)

IDENTITY_TTL = 900
PROFILE_TTL = 180
INBOX_TTL = 90
MESSAGES_TTL = 60

INBOX_PAGE_SIZE = 6
MESSAGE_PAGE_SIZE = 6
MAX_PRIVATE_DIALOGS = 80
MAX_DIALOG_SCAN = 250
MAX_GIFTS = 30
MAX_TEXT_CHARS = 3900

TELEGRAM_OFFICIAL_ID = 777000


class ExplorerError(Exception):
    """User-facing failure while loading account explorer pages."""

    def __init__(self, code: str, detail: str | None = None):
        super().__init__(code)
        self.code = code
        self.detail = detail


def identity_cache_key(account_id: int) -> str:
    return f"identity:{account_id}"


def profile_cache_key(account_id: int) -> str:
    return f"profile:{account_id}"


def inbox_cache_key(account_id: int) -> str:
    return f"inbox:{account_id}"


def messages_cache_key(account_id: int, chat_id: int) -> str:
    return f"dm:{account_id}:{chat_id}"


def get_cached_identity(account_id: int) -> Optional[dict]:
    data = cache_store.get_json(identity_cache_key(account_id))
    return data if isinstance(data, dict) else None


def display_name(identity: Optional[dict], fallback: str = "") -> str:
    if not identity:
        return fallback
    name = (identity.get("full_name") or "").strip()
    return name or fallback


def _map_client_error(exc: Exception, account_id: int | None = None) -> ExplorerError:
    log.error(f"Explorer client error for account {account_id}: {type(exc).__name__}: {exc}", exc_info=exc)
    if isinstance(exc, ExplorerError):
        error = exc
    elif isinstance(exc, TwoStepError):
        error = ExplorerError(exc.code, exc.detail)
    elif isinstance(exc, AUTH_ERRORS):
        error = ExplorerError("session_invalid", str(exc))
    elif isinstance(exc, FloodWait):
        error = ExplorerError("flood_wait", str(getattr(exc, "value", "")))
    elif isinstance(exc, (asyncio.TimeoutError, Timeout, ConnectionError, OSError)):
        error = ExplorerError("connect_failed", str(exc))
    elif isinstance(exc, RPCError):
        error = ExplorerError("unexpected", f"RPCError ({exc.MESSAGE or type(exc).__name__}): {exc}")
    else:
        error = ExplorerError("unexpected", str(exc))
    if error.code == "session_invalid" and account_id is not None:
        mark_session_invalid(account_id, error.detail or str(exc))
    return error


def _user_full_name(user) -> str:
    parts = [getattr(user, "first_name", None) or "", getattr(user, "last_name", None) or ""]
    return " ".join(part for part in parts if part).strip()


def _collectible_usernames(user) -> list[str]:
    names = []
    for item in getattr(user, "usernames", None) or []:
        username = getattr(item, "username", None)
        if not username:
            continue
        if getattr(item, "editable", True) is False:
            names.append(username)
    return names


def _serialize_identity(user, chat=None) -> dict:
    full = _user_full_name(user)
    emoji_status = getattr(user, "emoji_status", None)
    return {
        "user_id": getattr(user, "id", None),
        "first_name": getattr(user, "first_name", None) or "",
        "last_name": getattr(user, "last_name", None) or "",
        "full_name": full or (getattr(user, "username", None) and f"@{user.username}") or "",
        "username": getattr(user, "username", None) or "",
        "phone": getattr(user, "phone_number", None) or "",
        "is_premium": bool(getattr(user, "is_premium", False)),
        "language_code": getattr(user, "language_code", None) or "",
        "bio": (getattr(chat, "bio", None) or getattr(chat, "description", None) or "") if chat else "",
        "gift_count": getattr(chat, "gift_count", None) if chat else None,
        "collectible_usernames": _collectible_usernames(user),
        "emoji_status_title": getattr(emoji_status, "title", None) if emoji_status else None,
        "emoji_status_name": getattr(emoji_status, "name", None) if emoji_status else None,
        "has_photo": bool(getattr(user, "photo", None) or (chat and getattr(chat, "photo", None))),
    }


def _gift_title(gift) -> str:
    title = getattr(gift, "title", None) or getattr(gift, "name", None)
    if title:
        return str(title)
    sticker = getattr(gift, "sticker", None)
    emoji = getattr(sticker, "emoji", None) if sticker else None
    if emoji:
        return str(emoji)
    return "Gift"


def serialize_gift(gift) -> dict:
    from_user = getattr(gift, "from_user", None)
    return {
        "title": _gift_title(gift),
        "name": getattr(gift, "name", None) or "",
        "number": getattr(gift, "number", None) or getattr(gift, "collectible_id", None),
        "is_upgraded": bool(getattr(gift, "is_upgraded", False)),
        "is_saved": bool(getattr(gift, "is_saved", False)),
        "is_pinned": bool(getattr(gift, "is_pinned", False)),
        "is_limited": bool(getattr(gift, "is_limited", False)),
        "price": getattr(gift, "price", None),
        "from_name": _user_full_name(from_user) if from_user else "",
        "from_username": getattr(from_user, "username", None) if from_user else "",
    }


def format_gift_line(gift: dict) -> str:
    icon = "💎" if gift.get("is_upgraded") else "🎁"
    title = escape(str(gift.get("title") or "Gift"))
    number = gift.get("number")
    if number:
        title = f"{title} #{escape(str(number))}"
    extras = []
    if gift.get("is_pinned"):
        extras.append("📌")
    if gift.get("is_saved"):
        extras.append("👤")
    sender = gift.get("from_name") or gift.get("from_username")
    if sender:
        extras.append(escape(str(sender)))
    suffix = f" · {' · '.join(extras)}" if extras else ""
    return f"{icon} {title}{suffix}"


def _message_media_kind(message) -> Optional[str]:
    media = getattr(message, "media", None)
    if media is None:
        return None
    if isinstance(media, MessageMediaType):
        return media.name.lower()
    return str(media).split(".")[-1].lower()


def serialize_message(message) -> dict:
    text = getattr(message, "text", None) or getattr(message, "caption", None) or ""
    date = getattr(message, "date", None)
    from_user = getattr(message, "from_user", None)
    return {
        "id": getattr(message, "id", None),
        "outgoing": bool(getattr(message, "outgoing", False)),
        "text": text,
        "media": _message_media_kind(message),
        "date": date.isoformat() if isinstance(date, datetime) else None,
        "from_name": _user_full_name(from_user) if from_user else "",
    }


def serialize_dialog(dialog) -> Optional[dict]:
    chat = getattr(dialog, "chat", None)
    if chat is None or getattr(chat, "type", None) != ChatType.PRIVATE:
        return None
    top = getattr(dialog, "top_message", None)
    chat_id = getattr(chat, "id", None)
    if chat_id is None:
        return None
    is_self = bool(getattr(chat, "is_self", False))
    return {
        "chat_id": int(chat_id),
        "name": (getattr(chat, "full_name", None) or getattr(chat, "first_name", None) or getattr(chat, "title", None) or "").strip(),
        "username": getattr(chat, "username", None) or "",
        "is_bot": bool(getattr(chat, "is_bot", False)),
        "is_self": is_self,
        "is_official": int(chat_id) == TELEGRAM_OFFICIAL_ID,
        "unread": int(getattr(dialog, "unread_messages_count", 0) or 0),
        "preview": (getattr(top, "text", None) or getattr(top, "caption", None) or "") if top else "",
        "preview_media": _message_media_kind(top) if top else None,
        "date": top.date.isoformat() if top and getattr(top, "date", None) else None,
    }


def dialog_icon(dialog: dict) -> str:
    if dialog.get("is_self"):
        return "💾"
    if dialog.get("is_official"):
        return "📢"
    if dialog.get("is_bot"):
        return "🤖"
    if dialog.get("unread"):
        return "🔵"
    return "💬"


def dialog_title(dialog: dict, saved_label: str) -> str:
    if dialog.get("is_self"):
        return saved_label
    if dialog.get("is_official"):
        return "Telegram"
    return (dialog.get("name") or dialog.get("username") or str(dialog.get("chat_id"))).strip()


def format_dialog_button(dialog: dict, saved_label: str, max_len: int = 42) -> str:
    title = dialog_title(dialog, saved_label)
    unread = dialog.get("unread") or 0
    badge = f" ({unread})" if unread else ""
    text = f"{dialog_icon(dialog)} {title}{badge}"
    if len(text) > max_len:
        text = text[: max_len - 1] + "…"
    return text


def _media_label(kind: Optional[str], _: Callable[[str], str]) -> str:
    labels = {
        "photo": _("📷 Photo"),
        "video": _("🎬 Video"),
        "video_note": _("🎬 Video"),
        "voice": _("🎤 Voice"),
        "audio": _("🎧 Audio"),
        "document": _("📄 File"),
        "sticker": _("😊 Sticker"),
        "animation": _("GIF"),
        "location": _("📍 Location"),
        "venue": _("📍 Location"),
        "contact": _("👤 Contact"),
        "web_page": _("🔗 Link"),
        "poll": _("📊 Poll"),
    }
    if not kind:
        return ""
    return labels.get(kind, _("📎 Media"))


def format_message_line(message: dict, _: Callable[[str], str], you_label: str) -> str:
    arrow = "→" if message.get("outgoing") else "←"
    who = you_label if message.get("outgoing") else (message.get("from_name") or "")
    body = (message.get("text") or "").strip()
    if body:
        body = escape(body.replace("\n", " "))
        if len(body) > 180:
            body = body[:179] + "…"
    else:
        body = escape(_media_label(message.get("media"), _)) or _("(no text)")
    stamp = ""
    raw_date = message.get("date")
    if raw_date:
        try:
            dt = datetime.fromisoformat(raw_date)
            stamp = dt.strftime("%m-%d %H:%M")
        except (TypeError, ValueError):
            stamp = ""
    prefix = f"<code>{escape(stamp)}</code> " if stamp else ""
    who_bit = f"<i>{escape(who)}</i> " if who else ""
    return f"{prefix}{arrow} {who_bit}{body}"


def format_session_health_text(account: dict | None, _: Callable[[str], str]) -> str:
    """Banner shown on account details when the Telegram session is dead."""
    if not session_is_invalid(account):
        return ""
    return (
        "\n\n"
        + _("⚠️ <b>Account invalid</b>")
        + "\n"
        + _(
            "This session has expired or is no longer valid. You may need to sign in again — delete the account and add it once more."
        )
    )


def trim_html(text: str, limit: int = MAX_TEXT_CHARS) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def paginate(items: list, page: int, page_size: int) -> tuple[list, int, int]:
    total = len(items)
    pages = max(1, (total + page_size - 1) // page_size) if total else 1
    page = max(0, min(page, pages - 1))
    start = page * page_size
    return items[start:start + page_size], page, pages


async def _catch_up_security_messages(client, account_id: int) -> None:
    """Forward missed official login/security messages using an already-open client."""
    try:
        from src.code_monitor import code_monitor_manager
        from src.database import get_account_runtime_details

        details = get_account_runtime_details(account_id)
        if not details:
            return
        await code_monitor_manager.catch_up_with_client(client, details)
    except Exception as e:
        log.warning(f"Could not catch up security messages for account {account_id}: {e}")


import asyncio

async def fetch_identity(account_id: int, force: bool = False) -> dict:
    if not force:
        cached = get_cached_identity(account_id)
        if cached:
            return cached
    try:
        async with open_account_client(account_id) as client:
            res = await asyncio.gather(
                client.get_me(),
                client.get_chat("me"),
                return_exceptions=True
            )
            me = res[0]
            if isinstance(me, Exception):
                raise me
            chat = res[1] if not isinstance(res[1], Exception) else None
            identity = _serialize_identity(me, chat)
    except Exception as e:
        raise _map_client_error(e, account_id) from e
    mark_session_ok(account_id)
    cache_store.set_json(identity_cache_key(account_id), identity, IDENTITY_TTL)
    return identity


async def fetch_profile(account_id: int, force: bool = False) -> dict:
    key = profile_cache_key(account_id)
    if not force:
        cached = cache_store.get_json(key)
        if isinstance(cached, dict) and cached.get("identity"):
            return cached
    try:
        async with open_account_client(account_id) as client:
            res = await asyncio.gather(
                client.get_me(),
                client.get_chat("me"),
                return_exceptions=True
            )
            me = res[0]
            if isinstance(me, Exception):
                raise me
            chat = res[1] if not isinstance(res[1], Exception) else None
            identity = _serialize_identity(me, chat)

            gifts: list[dict] = []
            gifts_error = None
            total_gifts = identity.get("gift_count")

            async def _load_gifts_task():
                nonlocal gifts, gifts_error, total_gifts
                try:
                    async for gift in client.get_chat_gifts("me", limit=MAX_GIFTS):
                        gifts.append(serialize_gift(gift))
                except Exception as e:
                    gifts_error = str(e)
                    log.warning(f"Could not load gifts for account {account_id}: {e}")
                if total_gifts is None:
                    try:
                        total_gifts = await client.get_chat_gifts_count("me")
                    except Exception:
                        total_gifts = len(gifts)

            try:
                await asyncio.wait_for(_load_gifts_task(), timeout=5.0)
            except asyncio.TimeoutError:
                gifts_error = "timeout"
                if total_gifts is None:
                    total_gifts = len(gifts)

            asyncio.create_task(_catch_up_security_messages(client, account_id))
    except Exception as e:
        raise _map_client_error(e, account_id) from e

    mark_session_ok(account_id)
    cache_store.set_json(identity_cache_key(account_id), identity, IDENTITY_TTL)
    payload = {
        "identity": identity,
        "gifts": gifts,
        "gifts_error": gifts_error,
        "total_gifts": total_gifts,
    }
    cache_store.set_json(key, payload, PROFILE_TTL)
    return payload


async def fetch_private_dialogs(account_id: int, force: bool = False) -> list[dict]:
    key = inbox_cache_key(account_id)
    if not force:
        cached = cache_store.get_json(key)
        if isinstance(cached, list):
            return cached
    dialogs: list[dict] = []
    try:
        async with open_account_client(account_id) as client:
            scanned = 0
            async for dialog in client.get_dialogs():
                scanned += 1
                item = serialize_dialog(dialog)
                if item:
                    dialogs.append(item)
                    if len(dialogs) >= MAX_PRIVATE_DIALOGS:
                        break
                if scanned >= MAX_DIALOG_SCAN:
                    break
            await _catch_up_security_messages(client, account_id)
    except Exception as e:
        raise _map_client_error(e, account_id) from e
    mark_session_ok(account_id)
    dialogs.sort(key=lambda d: (d.get("unread") or 0, d.get("date") or ""), reverse=True)
    cache_store.set_json(key, dialogs, INBOX_TTL)
    return dialogs


async def fetch_private_messages(account_id: int, chat_id: int, force: bool = False) -> dict[str, Any]:
    key = messages_cache_key(account_id, chat_id)
    if not force:
        cached = cache_store.get_json(key)
        if isinstance(cached, dict) and isinstance(cached.get("messages"), list):
            return cached
    messages: list[dict] = []
    chat_meta = {"chat_id": chat_id, "name": "", "username": "", "is_self": False, "is_bot": False}
    try:
        async with open_account_client(account_id) as client:
            try:
                chat = await client.get_chat(chat_id)
                chat_meta.update({
                    "name": (
                        getattr(chat, "full_name", None)
                        or getattr(chat, "first_name", None)
                        or getattr(chat, "title", None)
                        or ""
                    ).strip(),
                    "username": getattr(chat, "username", None) or "",
                    "is_self": bool(getattr(chat, "is_self", False)),
                    "is_bot": bool(getattr(chat, "is_bot", False)),
                    "is_official": int(chat_id) == TELEGRAM_OFFICIAL_ID,
                })
            except Exception as e:
                log.debug(f"get_chat({chat_id}) failed for account {account_id}: {e}")
            async for message in client.get_chat_history(chat_id, limit=36):
                messages.append(serialize_message(message))
    except Exception as e:
        raise _map_client_error(e, account_id) from e
    mark_session_ok(account_id)
    payload = {"chat": chat_meta, "messages": messages}
    cache_store.set_json(key, payload, MESSAGES_TTL)
    return payload


async def fetch_active_sessions(account_id: int) -> list[dict]:
    """Fetch active Telegram authorizations (sessions/devices) for an account."""
    sessions = []
    try:
        async with open_account_client(account_id) as client:
            from pyrogram import raw
            res = await client.invoke(raw.functions.account.GetAuthorizations())
            authorizations = getattr(res, "authorizations", []) or []
            for auth in authorizations:
                date_created = getattr(auth, "date_created", 0) or 0
                date_active = getattr(auth, "date_active", 0) or 0
                sessions.append({
                    "hash": getattr(auth, "hash", 0),
                    "device_model": getattr(auth, "device_model", "") or "Unknown Device",
                    "platform": getattr(auth, "platform", "") or "",
                    "system_version": getattr(auth, "system_version", "") or "",
                    "app_name": getattr(auth, "app_name", "") or "",
                    "app_version": getattr(auth, "app_version", "") or "",
                    "date_created": datetime.fromtimestamp(date_created, timezone.utc).isoformat() if date_created else None,
                    "date_active": datetime.fromtimestamp(date_active, timezone.utc).isoformat() if date_active else None,
                    "ip": getattr(auth, "ip", "") or "",
                    "country": getattr(auth, "country", "") or "",
                    "region": getattr(auth, "region", "") or "",
                    "current": bool(getattr(auth, "current", False)),
                    "official_app": bool(getattr(auth, "official_app", False)),
                })
    except Exception as e:
        raise _map_client_error(e, account_id) from e

    mark_session_ok(account_id)
    return sessions


async def revoke_session(account_id: int, hash_val: int) -> bool:
    """Terminates an active session/authorization by its hash. Prevent revoking current."""
    try:
        async with open_account_client(account_id) as client:
            from pyrogram import raw
            res = await client.invoke(raw.functions.account.ResetAuthorization(hash=hash_val))
            return bool(res)
    except Exception as e:
        raise _map_client_error(e, account_id) from e


def format_profile_text(profile: dict, phone: str, _: Callable[[str], str]) -> str:
    identity = profile.get("identity") or {}
    name = escape(display_name(identity, phone or _("Unknown")))
    lines = [_("👤 <b>{name}</b>").format(name=name)]
    username = identity.get("username")
    if username:
        lines.append(_("🔗 @{username}").format(username=escape(username)))
    phone_value = identity.get("phone") or phone
    if phone_value:
        lines.append(_("📱 <code>{phone}</code>").format(phone=escape(str(phone_value))))
    if identity.get("user_id"):
        lines.append(_("🆔 <code>{user_id}</code>").format(user_id=identity["user_id"]))
    if identity.get("is_premium"):
        lines.append(_("⭐ Telegram Premium"))
    if identity.get("language_code"):
        lines.append(_("🌐 Language: {lang}").format(lang=escape(str(identity["language_code"]))))
    emoji_title = identity.get("emoji_status_title") or identity.get("emoji_status_name")
    if emoji_title:
        lines.append(_("🎭 {status}").format(status=escape(str(emoji_title))))
    bio = (identity.get("bio") or "").strip()
    if bio:
        lines.append("")
        lines.append(_("📝 {bio}").format(bio=escape(bio)))

    collectibles = identity.get("collectible_usernames") or []
    if collectibles:
        lines.append("")
        lines.append(_("✨ <b>Collectible usernames</b>"))
        for item in collectibles:
            lines.append(f"• @{escape(item)}")

    gifts = profile.get("gifts") or []
    total = profile.get("total_gifts")
    if total is None:
        total = len(gifts)
    lines.append("")
    lines.append(_("🎁 <b>Gifts & collectibles</b> ({count})").format(count=total))
    if profile.get("gifts_error") and not gifts:
        lines.append(_("Could not load gifts right now."))
    elif not gifts:
        lines.append(_("No gifts or collectibles on this profile."))
    else:
        shown = gifts[:12]
        lines.extend(f"• {format_gift_line(gift)}" for gift in shown)
        remaining = max(0, int(total) - len(shown))
        if remaining:
            lines.append(_("…and {count} more").format(count=remaining))
    return trim_html("\n".join(lines))
