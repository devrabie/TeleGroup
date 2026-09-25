"""Plan allowlists and per-account plugin toggles.

``groups`` and ``codemon`` keep the existing account columns so the current
group-creation and code-monitor buttons stay in sync with the plugins screen.
Other plugins use ``account_plugins``, falling back to the plugin default.
"""

from __future__ import annotations

import logging
from typing import Any

from src.runtime.plugins import (
    CODEMON_PLUGIN,
    GROUPS_PLUGIN,
    Plugin,
    all_plugins,
    get_plugin,
    load_plugins,
)
from src.runtime.store import (
    get_plugin_account,
    load_allowed_plugins,
    load_plugin_overrides,
    upsert_account_plugin,
)

log = logging.getLogger(__name__)


def _enabled_flag(
    plugin: Plugin,
    account: dict[str, Any],
    override: bool | None,
    allowed: set[str],
) -> bool:
    if plugin.meta.name not in allowed:
        return False
    if plugin.meta.name == GROUPS_PLUGIN:
        return bool(account.get("is_active"))
    if plugin.meta.name == CODEMON_PLUGIN:
        return bool(account.get("code_monitor_enabled"))
    if override is None:
        return plugin.meta.default_enabled
    return bool(override)


def plugin_is_enabled(account_id: int, plugin: Plugin) -> bool:
    account = get_plugin_account(account_id)
    if account is None or account.get("deleted"):
        return False
    plan_id = account.get("plan_id")
    if plan_id is None:
        return False
    allowed = load_allowed_plugins({int(plan_id)}).get(int(plan_id), set())
    overrides = load_plugin_overrides([account_id]).get(account_id, {})
    override = overrides.get(plugin.meta.name)
    return _enabled_flag(plugin, account, override, allowed)


def accounts_that_should_run(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep candidates that have at least one enabled plugin."""
    load_plugins()
    plugins = all_plugins()
    if not candidates or not plugins:
        return []
    plan_ids = {int(row["plan_id"]) for row in candidates if row.get("plan_id") is not None}
    overrides = load_plugin_overrides([int(row["account_id"]) for row in candidates])
    allowed_by_plan = load_allowed_plugins(plan_ids)
    selected: list[dict[str, Any]] = []
    for row in candidates:
        plan_id = row.get("plan_id")
        if plan_id is None:
            continue
        allowed = allowed_by_plan.get(int(plan_id), set())
        account_overrides = overrides.get(int(row["account_id"]), {})
        if any(
            _enabled_flag(plugin, row, account_overrides.get(plugin.meta.name), allowed)
            for plugin in plugins
        ):
            selected.append(row)
    return selected


def list_plugin_views(account_id: int, language: str) -> list[dict[str, Any]] | None:
    account = get_plugin_account(account_id)
    if account is None or account.get("deleted"):
        return None
    load_plugins()
    plan_id = account.get("plan_id")
    allowed = load_allowed_plugins({int(plan_id)}).get(int(plan_id), set()) if plan_id else set()
    overrides = load_plugin_overrides([account_id]).get(account_id, {})
    views: list[dict[str, Any]] = []
    for plugin in all_plugins():
        name = plugin.meta.name
        plan_allows = name in allowed
        stored = _enabled_flag(plugin, account, overrides.get(name), allowed | {name})
        views.append(
            {
                "name": name,
                "description": plugin.meta.description(language),
                "commands": [command.name for command in plugin.meta.commands],
                "allowed": plan_allows,
                "enabled": bool(stored) and plan_allows,
                "stored_enabled": bool(stored),
            }
        )
    return views


def list_plan_plugin_views(plan_id: int, language: str) -> list[dict[str, Any]] | None:
    from src.database import get_plan_by_id

    if get_plan_by_id(plan_id) is None:
        return None
    load_plugins()
    allowed = load_allowed_plugins({plan_id}).get(plan_id, set())
    return [
        {
            "name": plugin.meta.name,
            "description": plugin.meta.description(language),
            "allowed": plugin.meta.name in allowed,
        }
        for plugin in all_plugins()
    ]


def set_account_plugin(account_id: int, actor_telegram_id: int, plugin_name: str) -> str:
    """Flip a plugin. Returns enabled, disabled, locked, missing, or denied."""
    from src.database import toggle_account_status, toggle_code_monitor, user_owns_account

    if not user_owns_account(account_id, actor_telegram_id):
        return "denied"
    plugin = get_plugin(plugin_name)
    if plugin is None:
        return "missing"
    account = get_plugin_account(account_id)
    if account is None or account.get("deleted"):
        return "missing"
    plan_id = account.get("plan_id")
    allowed = load_allowed_plugins({int(plan_id)}).get(int(plan_id), set()) if plan_id else set()
    if plugin.meta.name not in allowed:
        return "locked"
    if plugin.meta.name == GROUPS_PLUGIN:
        new_status = toggle_account_status(account_id, actor_telegram_id)
        if new_status is None:
            return "denied"
        return "enabled" if new_status else "disabled"
    if plugin.meta.name == CODEMON_PLUGIN:
        new_status = toggle_code_monitor(account_id, actor_telegram_id)
        if new_status is None:
            return "denied"
        return "enabled" if new_status else "disabled"
    overrides = load_plugin_overrides([account_id]).get(account_id, {})
    current = _enabled_flag(plugin, account, overrides.get(plugin.meta.name), allowed)
    upsert_account_plugin(account_id, plugin.meta.name, not current)
    return "disabled" if current else "enabled"
