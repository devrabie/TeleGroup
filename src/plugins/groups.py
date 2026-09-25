"""Scheduled supergroup creation on the runtime client.

The name, log row, next-run time, and error backoff match the previous job.
The job's random pause before an eligible account is kept here as well.
"""

from __future__ import annotations

import logging
import random
from collections.abc import Awaitable

from src.runtime.plugins import GROUPS_PLUGIN, AccountSession, Plugin, PluginMeta

log = logging.getLogger(__name__)

POLL_SECONDS = 300


class GroupsPlugin(Plugin):
    meta = PluginMeta(
        name=GROUPS_PLUGIN,
        description_en="Create supergroups on the saved schedule.",
        description_ar="إنشاء مجموعات خارقة وفق الجدول المحفوظ.",
        default_enabled=False,
    )

    def spawn(self, session: AccountSession) -> Awaitable[None]:
        return self._run(session)

    async def _run(self, session: AccountSession) -> None:
        while not session.stopped():
            try:
                await self.tick(session)
            except Exception:
                log.exception("Group creation failed for account %s", session.account_id)
            await session.sleep(POLL_SECONDS)

    async def tick(self, session: AccountSession) -> None:
        from src.automation import create_group_using_client
        from src.database import get_eligible_accounts
        from src.runtime.gating import plugin_is_enabled

        if session.stopped() or not plugin_is_enabled(session.account_id, self):
            return
        if not _eligible(session.account_id, get_eligible_accounts()):
            return
        await session.sleep(random.uniform(5, 20))
        if session.stopped():
            return
        rows = _eligible(session.account_id, get_eligible_accounts())
        if not rows:
            return
        await create_group_using_client(session.client, rows[0], limiter=session.limiter)


def _eligible(account_id: int, rows: list[dict]) -> list[dict]:
    return [row for row in rows if int(row["account_id"]) == account_id]


plugin = GroupsPlugin()
