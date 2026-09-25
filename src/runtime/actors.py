"""Who may send a dot-command to a running account.

The account itself, its registered owner (the Telegram user who added it),
and rows in ``account_admins`` are accepted. Everyone else is ignored.
Sensitive commands stay with the owner and the account itself.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

# Profile, session, broadcast, stored account data, Stars, and admin management.
# Group moderation, downloads, and ordinary tools stay available to admins.
OWNER_ONLY_COMMANDS: frozenset[str] = frozenset(
    {
        "رفع ادمن",
        "addadmin",
        "تنزيل ادمن",
        "deladmin",
        "وضع الاسم",
        "setname",
        "وضع البايو",
        "setbio",
        "وضع الصورة",
        "setphoto",
        "كلمة السر",
        "cloudpass",
        "نقل ملكية",
        "نقل",
        "transfer",
        "انشاء كروب",
        "انشاء مجموعة",
        "create group",
        "creategroup",
        "انشاء قناة",
        "create channel",
        "createchannel",
        "مغادرة",
        "leave",
        "تخزين",
        "storage",
        "وضع التخزين",
        "setlog",
        "حذف رد عام",
        "ungfilter",
        "حذف رد",
        "unfilter",
        "اذاعة",
        "broadcast",
        "اذاعة خاص",
        "pbroadcast",
        "تأكيد الاذاعة",
        "confirmbroadcast",
        "ايقاف الاذاعة",
        "broadcaststop",
        "ارسل هدية",
        "ارسل",
        "gift",
        "تأكيد الهدية",
        "giftconfirm",
        "الغاء الهدية",
        "giftcancel",
    }
)


def is_owner_only(command_name: str) -> bool:
    return command_name in OWNER_ONLY_COMMANDS


def access_label(language: str, command_name: str) -> str:
    if is_owner_only(command_name):
        if language == "ar":
            return "صاحب الحساب فقط"
        return "Account owner only"
    if language == "ar":
        return "صاحب الحساب والمسؤولون"
    return "Owner and account admins"


def message_sender_id(message: Any) -> int | None:
    sender = getattr(message, "from_user", None)
    user_id = getattr(sender, "id", None)
    if user_id is None:
        return None
    try:
        return int(user_id)
    except (TypeError, ValueError):
        return None


def classify_actor(account_id: int, message: Any) -> str | None:
    """Return ``self``, ``owner``, ``admin``, or None when the sender is ignored."""
    from src.runtime.plugins import is_self_outgoing

    if is_self_outgoing(message):
        return "self"
    sender_id = message_sender_id(message)
    if sender_id is None:
        return None
    from src.runtime.store import get_plugin_account, is_account_admin

    account = get_plugin_account(account_id)
    if account is None or account.get("deleted"):
        return None
    owner = account.get("telegram_id")
    if owner is not None and int(owner) == sender_id:
        return "owner"
    if is_account_admin(account_id, sender_id):
        return "admin"
    return None


def is_trusted_sender(account_id: int, message: Any) -> bool:
    """Owner, admin, or the account itself. Used so guards do not punish them."""
    return classify_actor(account_id, message) is not None


class IncomingCommandLimit:
    """Cap commands that arrive from the owner or an admin, not from the account.

    The first command past the cap produces one notice. Further commands in
    that window are dropped with no reply, so a fast sender cannot flood chat.
    """

    def __init__(
        self,
        *,
        admin_limit: int = 6,
        owner_limit: int = 12,
        account_limit: int = 20,
        window: float = 30.0,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.admin_limit = admin_limit
        self.owner_limit = owner_limit
        self.account_limit = account_limit
        self.window = window
        self._clock = clock or time.monotonic
        self._hits: dict[tuple[int, int], list[float]] = {}
        self._notified: dict[tuple[int, int], float] = {}

    def reset(self) -> None:
        self._hits.clear()
        self._notified.clear()

    def decide(self, account_id: int, user_id: int, role: str) -> str:
        """Return ``ok``, ``notice`` (reply once), or ``limited`` (stay quiet)."""
        if role == "self":
            return "ok"
        now = self._clock()
        limit = self.admin_limit if role == "admin" else self.owner_limit
        user_key = (account_id, int(user_id))
        account_key = (account_id, 0)
        user_hits = self._prune(user_key, now)
        account_hits = self._prune(account_key, now)
        if len(user_hits) >= limit or len(account_hits) >= self.account_limit:
            until = self._notified.get(user_key, 0.0)
            if until <= now:
                self._notified[user_key] = now + self.window
                return "notice"
            return "limited"
        user_hits.append(now)
        account_hits.append(now)
        return "ok"

    def _prune(self, key: tuple[int, int], now: float) -> list[float]:
        fresh = [item for item in self._hits.get(key, []) if now - item < self.window]
        self._hits[key] = fresh
        return fresh


_LIMIT = IncomingCommandLimit()


def incoming_limit() -> IncomingCommandLimit:
    return _LIMIT
