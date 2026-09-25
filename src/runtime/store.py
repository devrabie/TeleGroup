"""Persistence for plugins, plan allowlists, leases, and runtime signals."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, select, update

from src.db.engine import session_scope
from src.db.models import (
    AccountPlugin,
    DeviceProfile,
    ManagedAccount,
    Plan,
    PlanPlugin,
    PluginSetting,
    RuntimeSignal,
    SessionLease,
    Subscription,
    User,
)
from src.runtime.signals import enqueue_signal

log = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _run(coro: Any) -> Any:
    """Use the same database loop and test ``DB_FILE`` override as the rest of the app."""
    from src.database import _run as db_run

    return db_run(coro)


def _kick() -> None:
    from src.runtime.signals import kick_runtime

    kick_runtime()


async def grant_default_plan_plugins(session: Any, plan_id: int) -> None:
    """Allow every currently registered plugin on a newly created plan."""
    from src.runtime.plugins import all_plugins, load_plugins

    load_plugins()
    for plugin in all_plugins():
        name = plugin.meta.name
        exists = await session.scalar(
            select(PlanPlugin.plugin_name).where(
                PlanPlugin.plan_id == plan_id,
                PlanPlugin.plugin_name == name,
            )
        )
        if exists is None:
            session.add(PlanPlugin(plan_id=plan_id, plugin_name=name))


def set_account_running(account_id: int, running: bool) -> None:
    async def _go() -> None:
        async with session_scope() as session:
            await session.execute(
                update(ManagedAccount)
                .where(ManagedAccount.id == account_id)
                .values(is_running=running)
            )

    try:
        _run(_go())
    except Exception:
        log.exception("Failed to set is_running=%s for account %s", running, account_id)


def account_is_running(account_id: int) -> bool:
    async def _go() -> bool:
        async with session_scope() as session:
            value = await session.scalar(
                select(ManagedAccount.is_running).where(ManagedAccount.id == account_id)
            )
            return bool(value)

    try:
        return bool(_run(_go()))
    except Exception:
        log.exception("Failed to read is_running for account %s", account_id)
        return False


def get_plugin_account(account_id: int) -> dict[str, Any] | None:
    """Owner, plan, and feature flags for gating. Sessions are not decrypted."""

    async def _go() -> dict[str, Any] | None:
        now = _now()
        async with session_scope() as session:
            row = (
                await session.execute(
                    select(
                        ManagedAccount.id,
                        ManagedAccount.is_active,
                        ManagedAccount.code_monitor_enabled,
                        ManagedAccount.session_status,
                        ManagedAccount.deleted_at,
                        User.telegram_id,
                        User.language_code,
                        Subscription.plan_id,
                    )
                    .join(User, ManagedAccount.user_id == User.id)
                    .outerjoin(
                        Subscription,
                        (Subscription.user_id == User.id)
                        & (Subscription.is_active.is_(True))
                        & (Subscription.end_date >= now),
                    )
                    .where(ManagedAccount.id == account_id)
                    .order_by(Subscription.end_date.desc())
                    .limit(1)
                )
            ).first()
            if row is None:
                return None
            return {
                "account_id": int(row.id),
                "is_active": bool(row.is_active),
                "code_monitor_enabled": bool(row.code_monitor_enabled),
                "session_status": row.session_status,
                "deleted": row.deleted_at is not None,
                "telegram_id": int(row.telegram_id),
                "language_code": row.language_code or "en",
                "plan_id": int(row.plan_id) if row.plan_id is not None else None,
            }

    try:
        return _run(_go())
    except Exception:
        log.exception("Failed to load plugin account %s", account_id)
        return None


def load_allowed_plugins(plan_ids: set[int]) -> dict[int, set[str]]:
    if not plan_ids:
        return {}

    async def _go() -> dict[int, set[str]]:
        async with session_scope() as session:
            rows = (
                await session.execute(
                    select(PlanPlugin.plan_id, PlanPlugin.plugin_name).where(
                        PlanPlugin.plan_id.in_(plan_ids)
                    )
                )
            ).all()
        found: dict[int, set[str]] = {plan_id: set() for plan_id in plan_ids}
        for plan_id, name in rows:
            found.setdefault(int(plan_id), set()).add(str(name))
        return found

    try:
        return _run(_go())
    except Exception:
        log.exception("Failed to load plan plugins")
        return {plan_id: set() for plan_id in plan_ids}


def load_plugin_overrides(account_ids: list[int]) -> dict[int, dict[str, bool]]:
    if not account_ids:
        return {}

    async def _go() -> dict[int, dict[str, bool]]:
        async with session_scope() as session:
            rows = (
                await session.execute(
                    select(
                        AccountPlugin.account_id,
                        AccountPlugin.plugin_name,
                        AccountPlugin.enabled,
                    ).where(AccountPlugin.account_id.in_(account_ids))
                )
            ).all()
        found: dict[int, dict[str, bool]] = {}
        for account_id, name, enabled in rows:
            found.setdefault(int(account_id), {})[str(name)] = bool(enabled)
        return found

    try:
        return _run(_go())
    except Exception:
        log.exception("Failed to load account plugin overrides")
        return {}


def upsert_account_plugin(account_id: int, plugin_name: str, enabled: bool) -> None:
    async def _go() -> None:
        async with session_scope() as session:
            row = await session.scalar(
                select(AccountPlugin).where(
                    AccountPlugin.account_id == account_id,
                    AccountPlugin.plugin_name == plugin_name,
                )
            )
            if row is None:
                session.add(
                    AccountPlugin(
                        account_id=account_id,
                        plugin_name=plugin_name,
                        enabled=enabled,
                    )
                )
            else:
                row.enabled = enabled
            await enqueue_signal(session, account_id, "reload")

    _run(_go())
    _kick()


def set_plan_plugin_allowed(plan_id: int, plugin_name: str, allowed: bool) -> bool:
    async def _go() -> bool:
        async with session_scope() as session:
            plan = await session.get(Plan, plan_id)
            if plan is None:
                return False
            row = await session.scalar(
                select(PlanPlugin).where(
                    PlanPlugin.plan_id == plan_id,
                    PlanPlugin.plugin_name == plugin_name,
                )
            )
            if allowed and row is None:
                session.add(PlanPlugin(plan_id=plan_id, plugin_name=plugin_name))
            elif not allowed and row is not None:
                await session.delete(row)
            await enqueue_signal(session, None, "reload")
            return True

    try:
        ok = bool(_run(_go()))
    except Exception:
        log.exception("Failed to update plan %s plugin %s", plan_id, plugin_name)
        return False
    if ok:
        _kick()
    return ok


def get_plugin_setting(account_id: int, plugin_name: str, key: str, default: Any = None) -> Any:
    async def _go() -> Any:
        async with session_scope() as session:
            raw = await session.scalar(
                select(PluginSetting.setting_value).where(
                    PluginSetting.account_id == account_id,
                    PluginSetting.plugin_name == plugin_name,
                    PluginSetting.setting_key == key,
                )
            )
            if raw is None:
                return default
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                return default

    try:
        return _run(_go())
    except Exception:
        log.exception("Failed to read plugin setting %s.%s for %s", plugin_name, key, account_id)
        return default


def set_plugin_setting(account_id: int, plugin_name: str, key: str, value: Any) -> None:
    encoded = json.dumps(value, ensure_ascii=False)

    async def _go() -> None:
        async with session_scope() as session:
            row = await session.scalar(
                select(PluginSetting).where(
                    PluginSetting.account_id == account_id,
                    PluginSetting.plugin_name == plugin_name,
                    PluginSetting.setting_key == key,
                )
            )
            if row is None:
                session.add(
                    PluginSetting(
                        account_id=account_id,
                        plugin_name=plugin_name,
                        setting_key=key,
                        setting_value=encoded,
                    )
                )
            else:
                row.setting_value = encoded
            await enqueue_signal(session, account_id, "reload")

    _run(_go())
    _kick()


def delete_plugin_setting(account_id: int, plugin_name: str, key: str) -> None:
    async def _go() -> None:
        async with session_scope() as session:
            await session.execute(
                delete(PluginSetting).where(
                    PluginSetting.account_id == account_id,
                    PluginSetting.plugin_name == plugin_name,
                    PluginSetting.setting_key == key,
                )
            )

    _run(_go())


def list_runtime_candidates(shard_id: int, shard_count: int) -> list[dict[str, Any]]:
    """Subscribed, non-deleted, valid sessions that belong to this shard."""
    from src.crypto import decrypt_session_value

    async def _go() -> list[dict[str, Any]]:
        now = _now()
        async with session_scope() as session:
            rows = (
                (
                    await session.execute(
                        select(
                            ManagedAccount.id.label("account_id"),
                            ManagedAccount.phone,
                            ManagedAccount.session_string,
                            ManagedAccount.proxy_id,
                            ManagedAccount.is_active,
                            ManagedAccount.code_monitor_enabled,
                            User.telegram_id,
                            User.language_code,
                            Subscription.plan_id,
                            DeviceProfile.device_model,
                            DeviceProfile.system_version,
                            DeviceProfile.app_version,
                            DeviceProfile.lang_code,
                            DeviceProfile.api_id,
                            DeviceProfile.api_hash,
                        )
                        .join(User, ManagedAccount.user_id == User.id)
                        .join(Subscription, User.id == Subscription.user_id)
                        .outerjoin(
                            DeviceProfile, ManagedAccount.device_profile_id == DeviceProfile.id
                        )
                        .where(
                            ManagedAccount.deleted_at.is_(None),
                            ManagedAccount.session_status != "invalid",
                            Subscription.is_active.is_(True),
                            Subscription.end_date >= now,
                            (ManagedAccount.id % shard_count) == shard_id,
                        )
                        .order_by(Subscription.end_date.desc())
                    )
                )
                .mappings()
                .all()
            )
        candidates: list[dict[str, Any]] = []
        seen: set[int] = set()
        for row in rows:
            account_id = int(row["account_id"])
            if account_id in seen:
                continue
            seen.add(account_id)
            candidates.append(
                {
                    "account_id": account_id,
                    "phone": row["phone"],
                    "session_string": decrypt_session_value(row["session_string"]),
                    "proxy_id": row["proxy_id"],
                    "is_active": bool(row["is_active"]),
                    "code_monitor_enabled": bool(row["code_monitor_enabled"]),
                    "telegram_id": int(row["telegram_id"]),
                    "language_code": row["language_code"] or "en",
                    "plan_id": int(row["plan_id"]),
                    "device_model": row["device_model"],
                    "system_version": row["system_version"],
                    "app_version": row["app_version"],
                    "lang_code": row["lang_code"],
                    "api_id": row["api_id"],
                    "api_hash": row["api_hash"],
                }
            )
        return candidates

    try:
        return _run(_go())
    except Exception:
        log.exception("Failed to list runtime accounts")
        return []


def prune_runtime_signals(max_age_seconds: int = 600) -> None:
    cutoff = _now() - timedelta(seconds=max_age_seconds)

    async def _go() -> None:
        async with session_scope() as session:
            await session.execute(delete(RuntimeSignal).where(RuntimeSignal.created_at < cutoff))

    try:
        _run(_go())
    except Exception:
        log.exception("Failed to prune runtime signals")


def acquire_session_lease(account_id: int, holder: str, ttl_seconds: int = 180) -> None:
    expires = _now() + timedelta(seconds=ttl_seconds)

    async def _go() -> None:
        async with session_scope() as session:
            row = await session.get(SessionLease, account_id)
            if row is None:
                session.add(
                    SessionLease(
                        account_id=account_id,
                        holder=holder,
                        expires_at=expires,
                        acked=False,
                    )
                )
            else:
                row.holder = holder
                row.expires_at = expires
                row.acked = False
            await enqueue_signal(session, account_id, "lease")

    _run(_go())
    _kick()


def ack_session_leases(account_ids: list[int]) -> None:
    if not account_ids:
        return

    async def _go() -> None:
        async with session_scope() as session:
            await session.execute(
                update(SessionLease)
                .where(SessionLease.account_id.in_(account_ids))
                .values(acked=True)
            )

    try:
        _run(_go())
    except Exception:
        log.exception("Failed to acknowledge session leases")


def release_session_lease(account_id: int) -> None:
    async def _go() -> None:
        async with session_scope() as session:
            await session.execute(delete(SessionLease).where(SessionLease.account_id == account_id))
            await enqueue_signal(session, account_id, "reload")

    try:
        _run(_go())
    except Exception:
        log.exception("Failed to release session lease for account %s", account_id)
        return
    _kick()


def lease_is_acked(account_id: int) -> bool:
    async def _go() -> bool:
        async with session_scope() as session:
            row = await session.get(SessionLease, account_id)
            if row is None or row.expires_at <= _now():
                return False
            return bool(row.acked)

    try:
        return bool(_run(_go()))
    except Exception:
        log.exception("Failed to read session lease for account %s", account_id)
        return False


def active_lease_ids() -> set[int]:
    now = _now()

    async def _go() -> set[int]:
        async with session_scope() as session:
            await session.execute(delete(SessionLease).where(SessionLease.expires_at <= now))
            rows = (await session.scalars(select(SessionLease.account_id))).all()
            return {int(account_id) for account_id in rows}

    try:
        return set(_run(_go()))
    except Exception:
        log.exception("Failed to list session leases")
        return set()
