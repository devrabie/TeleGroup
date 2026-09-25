"""Attach one incoming/outgoing handler while a plugin is enabled."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from src.runtime.gating import plugin_is_enabled
from src.runtime.plugins import AccountSession, Plugin

log = logging.getLogger(__name__)

CHECK_SECONDS = 2
# Command handlers use group -1 (see ``supervisor.COMMAND_HANDLER_GROUP``).
# Kurigram stops after the first match in a group, then continues with the
# next group, so listeners stay here and still receive outgoing text.
LISTENER_HANDLER_GROUP = 0


async def watch(
    session: AccountSession,
    plugin: Plugin,
    callback: Callable[[AccountSession, Any], Awaitable[None]],
) -> None:
    handler: Any = None
    try:
        while not session.stopped():
            enabled = plugin_is_enabled(session.account_id, plugin)
            add = getattr(session.client, "add_handler", None)
            remove = getattr(session.client, "remove_handler", None)
            if enabled and handler is None and add is not None:
                handler = _bind(session, callback)
                add(handler, LISTENER_HANDLER_GROUP)
            elif not enabled and handler is not None and remove is not None:
                _remove(remove, handler)
                handler = None
            await session.sleep(CHECK_SECONDS)
    finally:
        if handler is not None:
            remove = getattr(session.client, "remove_handler", None)
            if remove is not None:
                _remove(remove, handler)


def _bind(
    session: AccountSession,
    callback: Callable[[AccountSession, Any], Awaitable[None]],
) -> Any:
    from pyrogram.handlers import MessageHandler

    async def _entry(_client: Any, message: Any) -> None:
        try:
            await callback(session, message)
        except Exception:
            log.exception("Listener failed for account %s", session.account_id)

    # Kurigram only awaits coroutine-function callbacks. A sync function is
    # run in a thread pool and a returned coroutine is discarded.
    return MessageHandler(_entry)


def _remove(remove: Any, handler: Any) -> None:
    try:
        remove(handler, LISTENER_HANDLER_GROUP)
    except Exception:
        log.debug("Could not remove a plugin listener", exc_info=True)
