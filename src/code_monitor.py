"""Persistent Pyrogram clients that forward login/2FA notices to the bot owner."""

from __future__ import annotations

import asyncio
import logging
import random
from datetime import datetime, timedelta, timezone
from html import escape
from typing import Optional

from pyrogram import Client, filters
from pyrogram.enums import ChatType
from pyrogram.errors import (
    AuthKeyDuplicated,
    AuthKeyUnregistered,
    SessionRevoked,
    UserDeactivated,
    UserDeactivatedBan,
)
from pyrogram.handlers import EditedMessageHandler, MessageHandler
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from src import config
from src.database import (
    assign_account_proxy,
    get_account_runtime_details,
    get_code_monitor_accounts,
    get_proxy_string,
    get_random_proxy_id,
    mark_session_invalid,
    mark_session_ok,
    rotate_account_proxy,
)
from src.security_messages import (
    KIND_LOGIN_CODE,
    KIND_NEW_LOGIN,
    KIND_TELEGRAM_NOTICE,
    KIND_TWO_STEP,
    KIND_VERIFICATION,
    classify_security_message,
)
from src.translation import get_translation_func_for_user

log = logging.getLogger(__name__)

MAX_PROXY_RETRIES = 3
SEEN_TTL = timedelta(hours=6)
AUTH_ERRORS = (
    AuthKeyUnregistered,
    SessionRevoked,
    UserDeactivated,
    UserDeactivatedBan,
    AuthKeyDuplicated,
)


def is_socks_auth_error(exc: BaseException) -> bool:
    """True when a SOCKS5 proxy rejected the username/password."""
    parts = []
    seen: set[int] = set()
    current: Optional[BaseException] = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        parts.append(str(current))
        current = current.__cause__ or getattr(current, "__context__", None)
    text = " ".join(parts).lower()
    if "socks5 authentication failed" in text or "socks authentication failed" in text:
        return True
    return "authentication failed" in text and "socks" in text


def _parse_proxy_string(raw: str) -> tuple[str, int, Optional[str], Optional[str]]:
    """Return hostname, port, username, password from a proxy string."""
    if "://" in raw:
        raw = raw.split("://", 1)[1]

    username = None
    password = None

    if "@" in raw:
        auth, hostport = raw.rsplit("@", 1)
        if ":" in auth:
            username, password = auth.split(":", 1)
        elif auth:
            username = auth
        if ":" not in hostport:
            raise ValueError("missing port")
        hostname, port_s = hostport.rsplit(":", 1)
        if not hostname:
            raise ValueError("missing host")
        return hostname, int(port_s), username or None, password or None

    parts = raw.split(":")
    if len(parts) < 2:
        raise ValueError("expected host:port")
    hostname, port_s = parts[0], parts[1]
    if not hostname:
        raise ValueError("missing host")
    if len(parts) >= 4:
        username = parts[2] or None
        password = ":".join(parts[3:]) or None
    elif len(parts) == 3:
        username = parts[2] or None
    return hostname, int(port_s), username, password


def build_proxy_dict(proxy_string: Optional[str]) -> Optional[dict]:
    """
    Parse a proxy string into a Pyrogram SOCKS5 proxy dict.

    Credentials embedded in the string always win. PROXY_USERNAME / PROXY_PASSWORD
    are used only when the string has no username or password (e.g. host:port).
    """
    if not proxy_string or not str(proxy_string).strip():
        return None
    try:
        hostname, port, username, password = _parse_proxy_string(str(proxy_string).strip())
    except (ValueError, IndexError) as e:
        log.error(f"Invalid proxy format '{proxy_string}': {e}")
        return None

    if not username:
        username = getattr(config, "PROXY_USERNAME", None)
    if not password:
        password = getattr(config, "PROXY_PASSWORD", None)

    return {
        "scheme": "socks5",
        "hostname": hostname,
        "port": port,
        "username": username,
        "password": password,
    }


def message_text_from_update(message) -> str:
    """Best-effort plain text from a Pyrogram message, including service notices."""
    parts = []
    if getattr(message, "text", None):
        parts.append(message.text)
    if getattr(message, "caption", None):
        parts.append(message.caption)
    service = getattr(message, "service", None)
    if service and not parts:
        parts.append(str(service).replace("MessageServiceType.", "").replace("_", " ").title())
    return "\n".join(parts).strip()


def _kind_label(kind: str, _) -> str:
    labels = {
        KIND_LOGIN_CODE: _("Login / verification code"),
        KIND_TWO_STEP: _("Two-step verification change"),
        KIND_NEW_LOGIN: _("New login / new device"),
        KIND_TELEGRAM_NOTICE: _("Telegram security notice"),
        KIND_VERIFICATION: _("Verification notice"),
    }
    return labels.get(kind, _("Security notice"))


class CodeMonitorManager:
    """Keeps one long-lived Pyrogram client per account with code monitoring enabled."""

    def __init__(self):
        self._bot = None
        self._clients: dict[int, Client] = {}
        self._meta: dict[int, dict] = {}
        self._seen: dict[tuple, datetime] = {}
        self._start_locks: dict[int, asyncio.Lock] = {}

    def set_bot(self, bot) -> None:
        self._bot = bot

    def get_running_client(self, account_id: int) -> Optional[Client]:
        client = self._clients.get(account_id)
        if client is not None and getattr(client, "is_connected", False):
            return client
        return None

    def is_connected(self, account_id: int) -> bool:
        return self.get_running_client(account_id) is not None

    def _start_lock_for(self, account_id: int) -> asyncio.Lock:
        lock = self._start_locks.get(account_id)
        if lock is None:
            lock = asyncio.Lock()
            self._start_locks[account_id] = lock
        return lock

    def _prune_seen(self) -> None:
        cutoff = datetime.now(timezone.utc) - SEEN_TTL
        stale = [key for key, ts in self._seen.items() if ts < cutoff]
        for key in stale:
            self._seen.pop(key, None)

    def _mark_seen(self, account_id: int, chat_id: int, message_id: int) -> bool:
        """Return True if this message was already forwarded."""
        self._prune_seen()
        key = (account_id, chat_id, message_id)
        if key in self._seen:
            return True
        self._seen[key] = datetime.now(timezone.utc)
        return False

    async def start_account(self, account_details: dict) -> bool:
        account_id = account_details["account_id"]
        async with self._start_lock_for(account_id):
            existing = self.get_running_client(account_id)
            if existing is not None:
                return True

            await self._stop_client_unlocked(account_id)

            session_string = account_details.get("session_string")
            if not session_string:
                log.error(f"Cannot start code monitor for account {account_id}: missing session.")
                return False

            last_error = None
            original_proxy_id = account_details.get("proxy_id")
            proxy_id = original_proxy_id or get_random_proxy_id()
            for attempt in range(MAX_PROXY_RETRIES):
                proxy_string = get_proxy_string(proxy_id) if proxy_id else None
                proxy_dict = build_proxy_dict(proxy_string)
                if proxy_string and proxy_dict is None:
                    proxy_id = rotate_account_proxy(account_id, proxy_id)
                    continue

                client_name = f"monitor_{account_id}_{random.randint(1000, 9999)}"
                client = Client(
                    client_name,
                    session_string=session_string,
                    api_id=account_details.get("api_id") or config.API_ID,
                    api_hash=account_details.get("api_hash") or config.API_HASH,
                    device_model=account_details.get("device_model"),
                    system_version=account_details.get("system_version"),
                    app_version=account_details.get("app_version"),
                    lang_code=account_details.get("lang_code"),
                    in_memory=True,
                    proxy=proxy_dict,
                )
                handler = self._make_handler(account_details)
                incoming_private = filters.incoming & filters.private
                client.add_handler(MessageHandler(handler, incoming_private))
                client.add_handler(EditedMessageHandler(handler, incoming_private))

                try:
                    await asyncio.wait_for(client.start(), timeout=45.0)
                    if proxy_id and proxy_id != original_proxy_id:
                        assign_account_proxy(account_id, proxy_id)
                    self._clients[account_id] = client
                    self._meta[account_id] = {
                        "phone": account_details.get("phone"),
                        "telegram_id": account_details.get("telegram_id"),
                    }
                    log.info(f"Code monitor started for account {account_id}.")
                    mark_session_ok(account_id)
                    return True
                except AUTH_ERRORS as e:
                    last_error = e
                    log.error(f"Auth error starting code monitor for account {account_id}: {e}")
                    mark_session_invalid(account_id, str(e))
                    await self._safe_stop(client)
                    await self._notify_owner(
                        account_details.get("telegram_id"),
                        account_details.get("phone"),
                        "session_invalid",
                    )
                    return False
                except (asyncio.TimeoutError, ConnectionError, OSError) as e:
                    last_error = e
                    reason = "SOCKS5 authentication failed" if is_socks_auth_error(e) else str(e)
                    log.warning(
                        f"Connection error starting code monitor for account {account_id} "
                        f"(attempt {attempt + 1}/{MAX_PROXY_RETRIES}, proxy {proxy_id}): {reason}"
                    )
                    await self._safe_stop(client)
                    proxy_id = rotate_account_proxy(account_id, proxy_id)
                    await asyncio.sleep(1)
                except Exception as e:
                    last_error = e
                    if is_socks_auth_error(e):
                        log.warning(
                            f"SOCKS5 authentication failed for account {account_id} "
                            f"(attempt {attempt + 1}/{MAX_PROXY_RETRIES}, proxy {proxy_id})"
                        )
                        await self._safe_stop(client)
                        proxy_id = rotate_account_proxy(account_id, proxy_id)
                        await asyncio.sleep(1)
                        continue
                    log.error(
                        f"Unexpected error starting code monitor for account {account_id}: {e}",
                        exc_info=True,
                    )
                    await self._safe_stop(client)
                    break

            log.error(f"Failed to start code monitor for account {account_id}: {last_error}")
            return False

    async def stop_account(self, account_id: int) -> None:
        async with self._start_lock_for(account_id):
            await self._stop_client_unlocked(account_id)

    async def _stop_client_unlocked(self, account_id: int) -> None:
        client = self._clients.pop(account_id, None)
        self._meta.pop(account_id, None)
        if client is not None:
            await self._safe_stop(client)
            log.info(f"Code monitor stopped for account {account_id}.")

    async def stop_all(self) -> None:
        account_ids = list(self._clients.keys())
        for account_id in account_ids:
            await self.stop_account(account_id)

    async def on_enabled(self, account_id: int) -> bool:
        details = get_account_runtime_details(account_id)
        if not details:
            return False
        return await self.start_account(details)

    async def sync(self) -> None:
        """Start monitors that should be running and stop the rest."""
        desired = {acc["account_id"]: acc for acc in get_code_monitor_accounts()}
        running_ids = set(self._clients.keys())

        for account_id in running_ids - set(desired):
            await self.stop_account(account_id)

        for account_id, details in desired.items():
            client = self._clients.get(account_id)
            if client is None or not getattr(client, "is_connected", False):
                if client is not None:
                    await self.stop_account(account_id)
                await self.start_account(details)

    def _make_handler(self, account_details: dict):
        account_id = account_details["account_id"]
        phone = account_details.get("phone") or "?"
        owner_id = account_details.get("telegram_id")

        async def handler(client: Client, message) -> None:
            try:
                await self._handle_message(account_id, phone, owner_id, message)
            except Exception as e:
                log.error(
                    f"Error handling monitored message for account {account_id}: {e}",
                    exc_info=True,
                )

        return handler

    async def _handle_message(self, account_id: int, phone: str, owner_id: Optional[int], message) -> None:
        chat = getattr(message, "chat", None)
        is_private = bool(chat) and getattr(chat, "type", None) == ChatType.PRIVATE
        from_user = getattr(message, "from_user", None) or chat
        from_user_id = getattr(from_user, "id", None)
        username = getattr(from_user, "username", None)
        is_outgoing = bool(getattr(message, "outgoing", False))
        text = message_text_from_update(message)

        classification = classify_security_message(
            text=text,
            from_user_id=from_user_id,
            username=username,
            is_private=is_private,
            is_outgoing=is_outgoing,
        )
        if not classification:
            return

        chat_id = getattr(chat, "id", 0)
        message_id = getattr(message, "id", 0)
        if self._mark_seen(account_id, chat_id, message_id):
            return

        await self._forward_alert(
            owner_id=owner_id,
            phone=phone,
            from_user=from_user,
            text=text,
            classification=classification,
        )

    async def _forward_alert(self, owner_id, phone, from_user, text, classification) -> None:
        if not self._bot or not owner_id:
            log.warning("Cannot forward security alert: bot or owner missing.")
            return

        _ = get_translation_func_for_user(owner_id)
        sender_name = getattr(from_user, "first_name", None) or getattr(from_user, "title", None) or "Telegram"
        sender_username = getattr(from_user, "username", None)
        sender_id = getattr(from_user, "id", None)
        source = escape(str(sender_name))
        if sender_username:
            source += f" (@{escape(sender_username)})"
        if sender_id:
            source += f" [<code>{sender_id}</code>]"

        kind_label = _kind_label(classification["kind"], _)
        codes = classification.get("codes") or []
        body = escape(text) if text else _("(no text)")

        alert = (
            _("🔐 <b>Security alert</b>\n")
            + _("Account: <code>{phone}</code>\n").format(phone=escape(str(phone)))
            + _("Source: {source}\n").format(source=source)
            + _("Type: {kind}\n").format(kind=escape(kind_label))
        )
        if codes:
            alert += _("Code: {codes}\n").format(
                codes=" ".join(f"<code>{escape(c)}</code>" for c in codes)
            )
        alert += _("\n{body}").format(body=body)

        try:
            await self._bot.send_message(
                chat_id=owner_id,
                text=alert,
                parse_mode=ParseMode.HTML,
            )
        except Exception as e:
            log.error(f"Failed to send security alert to {owner_id}: {e}")

    async def _notify_owner(self, owner_id, phone, reason: str) -> None:
        if not self._bot or not owner_id:
            return
        _ = get_translation_func_for_user(owner_id)
        if reason == "session_invalid":
            text = _(
                "⚠️ Code monitor could not start for <code>{phone}</code> because the session is invalid or revoked. "
                "Please delete and re-add the account."
            ).format(phone=escape(str(phone or "")))
        else:
            text = _("⚠️ Code monitor failed for <code>{phone}</code>.").format(
                phone=escape(str(phone or ""))
            )
        try:
            await self._bot.send_message(chat_id=owner_id, text=text, parse_mode=ParseMode.HTML)
        except Exception as e:
            log.error(f"Failed to notify owner {owner_id} about monitor failure: {e}")

    @staticmethod
    async def _safe_stop(client: Client) -> None:
        try:
            if getattr(client, "is_connected", False):
                await client.stop()
        except Exception as e:
            log.debug(f"Error stopping monitor client: {e}")


code_monitor_manager = CodeMonitorManager()


def get_running_monitor_client(account_id: int) -> Optional[Client]:
    return code_monitor_manager.get_running_client(account_id)


async def run_code_monitor_sync(context: ContextTypes.DEFAULT_TYPE):
    """Job-queue entry point: keep monitor clients in sync with the database."""
    code_monitor_manager.set_bot(context.bot)
    try:
        await code_monitor_manager.sync()
    except Exception as e:
        log.error(f"Code monitor sync failed: {e}", exc_info=True)
