"""Login-code monitoring on the runtime's long-lived client.

Handlers, catch-up, and owner alerts stay in ``CodeMonitorManager``. This
plugin only attaches them while the account has monitoring enabled, so the
worker does not open a second session.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable
from typing import Any

from src.code_monitor import code_monitor_manager
from src.runtime.plugins import CODEMON_PLUGIN, AccountSession, Plugin, PluginMeta

log = logging.getLogger(__name__)

CHECK_SECONDS = 2


class CodeMonitorPlugin(Plugin):
    meta = PluginMeta(
        name=CODEMON_PLUGIN,
        description_en="Forward login codes and Telegram security notices.",
        description_ar="إعادة توجيه أكواد الدخول وتنبيهات أمان تيليجرام.",
        default_enabled=False,
    )

    def spawn(self, session: AccountSession) -> Awaitable[None]:
        return self._run(session)

    async def _run(self, session: AccountSession) -> None:
        handlers: list[Any] = []
        bound = False
        try:
            while not session.stopped():
                enabled = self._enabled(session.account_id)
                if enabled and not bound:
                    details = self._details(session)
                    handlers = code_monitor_manager.bind(session.client, details)
                    bound = True
                    await code_monitor_manager.catch_up_with_client(session.client, details)
                    log.info("Code monitor attached for account %s", session.account_id)
                elif not enabled and bound:
                    code_monitor_manager.unbind(session.client, handlers, session.account_id)
                    handlers = []
                    bound = False
                    log.info("Code monitor detached for account %s", session.account_id)
                await session.sleep(CHECK_SECONDS)
        finally:
            if bound:
                code_monitor_manager.unbind(session.client, handlers, session.account_id)

    def _enabled(self, account_id: int) -> bool:
        from src.runtime.gating import plugin_is_enabled

        return plugin_is_enabled(account_id, self)

    def _details(self, session: AccountSession) -> dict[str, Any]:
        from src.database import get_account_runtime_details

        details = get_account_runtime_details(session.account_id)
        if details:
            return details
        return session.details


plugin = CodeMonitorPlugin()
