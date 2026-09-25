"""Wake the account worker when control-bot state changes.

Postgres deployments also emit NOTIFY. The row is the durable hint; the worker
reconciles from account tables, so a missed notification is corrected on the
next poll. SQLite tests use the row and an in-process kick only.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text

from src.db.models import RuntimeSignal

log = logging.getLogger(__name__)

CHANNEL = "telegroup_runtime"


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


async def enqueue_signal(session: Any, account_id: int | None, event: str) -> None:
    """Insert a signal in the caller's transaction and NOTIFY on PostgreSQL."""
    session.add(RuntimeSignal(account_id=account_id, event=event[:32], created_at=_now()))
    await session.flush()
    connection = await session.connection()
    if connection.dialect.name != "postgresql":
        return
    payload = json.dumps({"event": event, "account_id": account_id})
    try:
        await session.execute(
            text("SELECT pg_notify(:channel, :payload)"),
            {"channel": CHANNEL, "payload": payload},
        )
    except Exception:
        log.exception("pg_notify failed for account %s", account_id)


def kick_runtime() -> None:
    """Ask an in-process supervisor to reconcile. No-op when this process has none."""
    try:
        from src.runtime.supervisor import request_reconcile

        request_reconcile()
    except Exception:
        log.debug("Could not wake the in-process account runtime", exc_info=True)
