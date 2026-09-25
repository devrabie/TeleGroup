"""Let the control bot borrow a session the worker is holding.

Interactive tools (profile, 2FA, channel tools) open their own client when the
runtime is in another process. They take a short lease first so the worker
disconnects and Telegram does not see two sessions.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

from src.config import get_settings
from src.runtime.store import (
    acquire_session_lease,
    lease_is_acked,
    release_session_lease,
)

log = logging.getLogger(__name__)


async def pause_remote_runtime(
    account_id: int,
    *,
    role: str | None = None,
    timeout: float = 20.0,
    poll_seconds: float = 0.25,
) -> bool:
    """Ask the worker to drop ``account_id``. Returns True when a lease was taken."""
    current_role = get_settings().runtime_role if role is None else role
    if current_role != "bot":
        return False
    acquire_session_lease(account_id, holder="bot")
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while True:
        if lease_is_acked(account_id):
            return True
        if loop.time() >= deadline:
            log.warning(
                "Timed out waiting for the worker to release account %s; continuing.",
                account_id,
            )
            return True
        await asyncio.sleep(poll_seconds)


async def resume_remote_runtime(account_id: int, *, paused: bool) -> None:
    if paused:
        release_session_lease(account_id)


@asynccontextmanager
async def use_account_client(
    account_id: int,
    factory: Callable[[], Any],
    *,
    method: str = "connect",
) -> AsyncIterator[Any]:
    """Reuse the in-process runtime client, or pause a remote worker and connect."""
    from src.code_monitor import get_running_monitor_client

    shared = get_running_monitor_client(account_id)
    if shared is not None:
        yield shared
        return

    paused = await pause_remote_runtime(account_id)
    client = factory()
    try:
        starter: Callable[[], Awaitable[Any]]
        if method == "start":
            starter = client.start
        else:
            starter = client.connect
        await starter()
        yield client
    finally:
        try:
            if method == "start":
                if getattr(client, "is_connected", False) or getattr(
                    client, "is_initialized", False
                ):
                    await client.stop()
            elif getattr(client, "is_connected", False):
                await client.disconnect()
        except Exception:
            log.debug(
                "Failed to close interactive client for account %s", account_id, exc_info=True
            )
        await resume_remote_runtime(account_id, paused=paused)
