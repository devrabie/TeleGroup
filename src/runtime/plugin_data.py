"""Rows for auto-replies, private-message permits, and group locks."""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import delete, select

from src.db.engine import session_scope
from src.db.models import AutoReply, ChatLock, PmPermit

log = logging.getLogger(__name__)


def _deleted(result: object) -> bool:
    return bool(getattr(result, "rowcount", 0))


GLOBAL_CHAT_ID = 0
MAX_RULES = 100


def _run(coro: Any) -> Any:
    from src.database import _run as db_run

    return db_run(coro)


def list_auto_replies(account_id: int, chat_id: int | None = None) -> list[dict[str, Any]]:
    async def _go() -> list[dict[str, Any]]:
        async with session_scope() as session:
            stmt = select(AutoReply).where(AutoReply.account_id == account_id)
            if chat_id is not None:
                stmt = stmt.where(AutoReply.chat_id == chat_id)
            rows = (await session.scalars(stmt.order_by(AutoReply.id))).all()
            return [
                {
                    "id": int(row.id),
                    "chat_id": int(row.chat_id),
                    "keyword": row.keyword,
                    "response": row.response,
                }
                for row in rows
            ]

    try:
        return list(_run(_go()))
    except Exception:
        log.exception("Failed to list auto-replies for account %s", account_id)
        return []


def upsert_auto_reply(account_id: int, chat_id: int, keyword: str, response: str) -> str:
    """Insert or replace a rule. Returns ``saved``, ``limit``, or ``invalid``."""
    keyword = keyword.strip()
    response = response.strip()
    if not keyword or not response or len(keyword) > 80 or len(response) > 3500:
        return "invalid"

    async def _go() -> str:
        async with session_scope() as session:
            row = await session.scalar(
                select(AutoReply).where(
                    AutoReply.account_id == account_id,
                    AutoReply.chat_id == chat_id,
                    AutoReply.keyword == keyword,
                )
            )
            if row is None:
                count = len(
                    (
                        await session.scalars(
                            select(AutoReply.id).where(
                                AutoReply.account_id == account_id,
                                AutoReply.chat_id == chat_id,
                            )
                        )
                    ).all()
                )
                if count >= MAX_RULES:
                    return "limit"
                session.add(
                    AutoReply(
                        account_id=account_id,
                        chat_id=chat_id,
                        keyword=keyword,
                        response=response,
                    )
                )
            else:
                row.response = response
            return "saved"

    try:
        return str(_run(_go()))
    except Exception:
        log.exception("Failed to save auto-reply for account %s", account_id)
        return "invalid"


def delete_auto_reply(account_id: int, chat_id: int, keyword: str) -> bool:
    keyword = keyword.strip()

    async def _go() -> bool:
        async with session_scope() as session:
            result = await session.execute(
                delete(AutoReply).where(
                    AutoReply.account_id == account_id,
                    AutoReply.chat_id == chat_id,
                    AutoReply.keyword == keyword,
                )
            )
            return _deleted(result)

    try:
        return bool(_run(_go()))
    except Exception:
        log.exception("Failed to delete auto-reply for account %s", account_id)
        return False


def get_pm_permit(account_id: int, user_id: int) -> dict[str, Any] | None:
    async def _go() -> dict[str, Any] | None:
        async with session_scope() as session:
            row = await session.get(PmPermit, (account_id, user_id))
            if row is None:
                return None
            return {
                "approved": bool(row.approved),
                "warnings": int(row.warnings),
                "blocked": bool(row.blocked),
            }

    try:
        return _run(_go())
    except Exception:
        log.exception("Failed to read PM permit %s/%s", account_id, user_id)
        return None


def set_pm_permit(
    account_id: int,
    user_id: int,
    *,
    approved: bool,
    warnings: int,
    blocked: bool,
) -> None:
    async def _go() -> None:
        async with session_scope() as session:
            row = await session.get(PmPermit, (account_id, user_id))
            if row is None:
                session.add(
                    PmPermit(
                        account_id=account_id,
                        user_id=user_id,
                        approved=approved,
                        warnings=warnings,
                        blocked=blocked,
                    )
                )
            else:
                row.approved = approved
                row.warnings = warnings
                row.blocked = blocked

    _run(_go())


def list_locks(account_id: int, chat_id: int) -> list[str]:
    async def _go() -> list[str]:
        async with session_scope() as session:
            rows = (
                await session.scalars(
                    select(ChatLock.lock_name).where(
                        ChatLock.account_id == account_id,
                        ChatLock.chat_id == chat_id,
                    )
                )
            ).all()
            return sorted(str(name) for name in rows)

    try:
        return list(_run(_go()))
    except Exception:
        log.exception("Failed to list locks for account %s chat %s", account_id, chat_id)
        return []


def add_lock(account_id: int, chat_id: int, lock_name: str) -> None:
    async def _go() -> None:
        async with session_scope() as session:
            row = await session.get(ChatLock, (account_id, chat_id, lock_name))
            if row is None:
                session.add(ChatLock(account_id=account_id, chat_id=chat_id, lock_name=lock_name))

    _run(_go())


def remove_lock(account_id: int, chat_id: int, lock_name: str) -> bool:
    async def _go() -> bool:
        async with session_scope() as session:
            result = await session.execute(
                delete(ChatLock).where(
                    ChatLock.account_id == account_id,
                    ChatLock.chat_id == chat_id,
                    ChatLock.lock_name == lock_name,
                )
            )
            return _deleted(result)

    try:
        return bool(_run(_go()))
    except Exception:
        log.exception("Failed to remove lock %s for account %s", lock_name, account_id)
        return False
