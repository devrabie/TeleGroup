"""Supervise one Kurigram client per managed account that should be online.

The control bot and the worker share state through the database. This process
owns the clients for ``worker_shard_id``. Invalid sessions are marked and the
owner is notified; other failures reconnect with exponential backoff.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from collections.abc import Awaitable, Callable, Coroutine
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from html import escape
from typing import Any

from pyrogram import filters
from pyrogram.handlers import MessageHandler

from src.code_monitor import AUTH_ERRORS, code_monitor_manager
from src.config import get_settings
from src.db.engine import current_database_url
from src.runtime.connect import ConnectFailed, SessionInvalid, connect_account
from src.runtime.flood import AccountLimiter, FloodDeferred
from src.runtime.gating import accounts_that_should_run
from src.runtime.notifier import BotNotifier, Notifier
from src.runtime.plugins import AccountSession, Dispatcher, Plugin, all_plugins, load_plugins
from src.runtime.signals import CHANNEL
from src.runtime.store import (
    ack_session_leases,
    active_lease_ids,
    list_runtime_candidates,
    prune_runtime_signals,
    set_account_running,
)

log = logging.getLogger(__name__)

_AUTH_NAMES = {error.__name__ for error in AUTH_ERRORS}
_CURRENT: Supervisor | None = None

# Kurigram runs the first matching handler in a group and then moves on to the
# next group. Commands sit alone in this group so listeners in other groups
# still see the same outgoing text. Saved Messages are not ``outgoing``;
# ``filters.me`` includes those (``from_user.is_self``) and ordinary sends.
COMMAND_HANDLER_GROUP = -1
_COMMAND_FILTER = (filters.outgoing | filters.me) & filters.text


def request_reconcile() -> None:
    current = _CURRENT
    if current is not None:
        current.wake_threadsafe()


def running_client(account_id: int) -> Any | None:
    current = _CURRENT
    if current is None:
        return None
    return current.client_for(account_id)


def _fingerprint(details: dict[str, Any]) -> tuple[Any, str]:
    raw = str(details.get("session_string") or "")
    digest = hashlib.sha256(raw.encode()).hexdigest()
    return (details.get("proxy_id"), digest)


def _is_auth_error(exc: BaseException) -> bool:
    if isinstance(exc, AUTH_ERRORS):
        return True
    return type(exc).__name__ in _AUTH_NAMES


def _asyncpg_dsn(url: str) -> str | None:
    prefix = "postgresql+asyncpg://"
    if url.startswith(prefix):
        return "postgresql://" + url[len(prefix) :]
    if url.startswith("postgresql://"):
        return url
    return None


@dataclass
class _Slot:
    task: asyncio.Task[None]
    stop_event: asyncio.Event
    refresh: asyncio.Event
    fingerprint: tuple[Any, str]
    client: Any | None = field(default=None)


class Supervisor:
    """Start, health-check, and stop the accounts that belong to this shard."""

    def __init__(
        self,
        *,
        notifier: Notifier | None = None,
        connector: Callable[[dict[str, Any]], Awaitable[Any]] | None = None,
        shard_id: int | None = None,
        shard_count: int | None = None,
        poll_seconds: float | None = None,
        health_interval: float | None = None,
        backoff_initial: float | None = None,
        backoff_max: float | None = None,
        sleeper: Callable[[float], Coroutine[Any, Any, None]] | None = None,
        plugins: list[Plugin] | None = None,
        listen: bool = True,
        stop_timeout: float = 10.0,
    ) -> None:
        settings = get_settings()
        self.notifier = notifier or BotNotifier()
        self.connector = connector or connect_account
        self.shard_id = settings.worker_shard_id if shard_id is None else shard_id
        self.shard_count = settings.worker_shard_count if shard_count is None else shard_count
        self.poll_seconds = settings.runtime_poll_seconds if poll_seconds is None else poll_seconds
        self.health_interval = (
            settings.runtime_health_seconds if health_interval is None else health_interval
        )
        self.backoff_initial = (
            settings.runtime_backoff_initial_seconds if backoff_initial is None else backoff_initial
        )
        self.backoff_max = (
            settings.runtime_backoff_max_seconds if backoff_max is None else backoff_max
        )
        self.sleeper = sleeper or asyncio.sleep
        self._plugins = plugins
        self.listen = listen
        self.stop_timeout = stop_timeout
        self._slots: dict[int, _Slot] = {}
        self._wake = asyncio.Event()
        self._shutdown = asyncio.Event()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._main_task: asyncio.Task[None] | None = None
        self._listen_task: asyncio.Task[None] | None = None
        self._limiters: dict[int, AccountLimiter] = {}

    def client_for(self, account_id: int) -> Any | None:
        slot = self._slots.get(account_id)
        if slot is None or slot.client is None:
            return None
        if not getattr(slot.client, "is_connected", False):
            return None
        return slot.client

    def wake_threadsafe(self) -> None:
        loop = self._loop
        if loop is None or loop.is_closed():
            return

        def _set() -> None:
            self._wake.set()
            for slot in self._slots.values():
                slot.refresh.set()

        loop.call_soon_threadsafe(_set)

    def _plugins_for_account(self) -> list[Plugin]:
        if self._plugins is not None:
            return self._plugins
        return all_plugins()

    def _limiter_for(self, account_id: int) -> AccountLimiter:
        limiter = self._limiters.get(account_id)
        if limiter is None:
            settings = get_settings()

            def _note_flood(seconds: int, recorded_id: int = account_id) -> None:
                _record_flood(recorded_id, seconds)

            limiter = AccountLimiter(
                account_id,
                min_interval=settings.flood_min_interval_seconds,
                retry_threshold=settings.flood_retry_threshold_seconds,
                on_flood=_note_flood,
            )
            self._limiters[account_id] = limiter
        return limiter

    async def start(self) -> None:
        global _CURRENT
        if self._main_task is not None:
            return
        load_plugins()
        self._loop = asyncio.get_running_loop()
        bot = getattr(self.notifier, "bot", None)
        if bot is not None:
            code_monitor_manager.set_bot(bot)
        _CURRENT = self
        self._main_task = asyncio.create_task(self._loop_main(), name="runtime-supervisor")
        if self.listen:
            self._listen_task = asyncio.create_task(self._listen_postgres(), name="runtime-listen")
        log.info(
            "Account runtime started for shard %s/%s",
            self.shard_id,
            self.shard_count,
        )

    async def stop(self) -> None:
        global _CURRENT
        self._shutdown.set()
        self._wake.set()
        if self._listen_task is not None:
            self._listen_task.cancel()
            await asyncio.gather(self._listen_task, return_exceptions=True)
            self._listen_task = None
        if self._main_task is not None:
            await asyncio.gather(self._main_task, return_exceptions=True)
            self._main_task = None
        for account_id in list(self._slots):
            await self._stop_slot(account_id)
        if _CURRENT is self:
            _CURRENT = None
        log.info("Account runtime stopped")

    async def reconcile(self) -> None:
        """Make running clients match the database for this shard."""
        if self._shutdown.is_set():
            return
        prune_runtime_signals()
        leased = {account_id for account_id in active_lease_ids() if self._owns(account_id)}
        for account_id in leased:
            await self._stop_slot(account_id)
        ack_session_leases(sorted(leased))

        candidates = accounts_that_should_run(
            list_runtime_candidates(self.shard_id, self.shard_count)
        )
        desired = {
            int(row["account_id"]): row
            for row in candidates
            if int(row["account_id"]) not in leased and self._owns(int(row["account_id"]))
        }
        for account_id in list(self._slots):
            if account_id not in desired:
                await self._stop_slot(account_id)
        for account_id, details in desired.items():
            fingerprint = _fingerprint(details)
            slot = self._slots.get(account_id)
            if slot is None:
                self._start_slot(account_id, details, fingerprint)
            elif slot.fingerprint != fingerprint:
                await self._stop_slot(account_id)
                self._start_slot(account_id, details, fingerprint)
            else:
                slot.refresh.set()

    def _owns(self, account_id: int) -> bool:
        if self.shard_count < 1:
            return False
        return account_id % self.shard_count == self.shard_id

    async def _loop_main(self) -> None:
        while not self._shutdown.is_set():
            self._wake.clear()
            try:
                await self.reconcile()
            except Exception:
                log.exception("Account reconcile failed")
            if self._shutdown.is_set():
                break
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=self.poll_seconds)
            except TimeoutError:
                continue

    def _start_slot(
        self,
        account_id: int,
        details: dict[str, Any],
        fingerprint: tuple[Any, str],
    ) -> None:
        stop_event = asyncio.Event()
        refresh = asyncio.Event()
        task = asyncio.create_task(
            self._run_account(account_id, details, stop_event, refresh),
            name=f"account-{account_id}",
        )
        self._slots[account_id] = _Slot(
            task=task,
            stop_event=stop_event,
            refresh=refresh,
            fingerprint=fingerprint,
        )

    async def _stop_slot(self, account_id: int) -> None:
        slot = self._slots.get(account_id)
        if slot is None:
            return
        slot.stop_event.set()
        slot.refresh.set()
        try:
            await asyncio.wait_for(asyncio.shield(slot.task), timeout=self.stop_timeout)
        except TimeoutError:
            slot.task.cancel()
            await asyncio.gather(slot.task, return_exceptions=True)
        except asyncio.CancelledError:
            slot.task.cancel()
            await asyncio.gather(slot.task, return_exceptions=True)
        self._slots.pop(account_id, None)
        self._limiters.pop(account_id, None)

    async def _run_account(
        self,
        account_id: int,
        details: dict[str, Any],
        stop_event: asyncio.Event,
        refresh: asyncio.Event,
    ) -> None:
        delay = self.backoff_initial
        while not stop_event.is_set() and not self._shutdown.is_set():
            if account_id in active_lease_ids():
                await self._interruptible_sleep(delay, stop_event)
                continue
            try:
                client = await self.connector(details)
            except SessionInvalid as exc:
                await self._invalidate(account_id, details, exc)
                return
            except Exception as exc:
                log.warning(
                    "Account %s failed to connect (%s). Retrying in %.1fs.",
                    account_id,
                    exc,
                    delay,
                )
                await self._interruptible_sleep(delay, stop_event)
                delay = min(delay * 2, self.backoff_max)
                continue

            if stop_event.is_set() or self._shutdown.is_set():
                await _safe_stop(client)
                return

            delay = self.backoff_initial
            slot = self._slots.get(account_id)
            if slot is not None:
                slot.client = client
            set_account_running(account_id, True)
            _mark_session_ok(account_id)
            invalid: SessionInvalid | None = None
            try:
                await self._serve(account_id, client, details, stop_event, refresh)
            except SessionInvalid as exc:
                invalid = exc
            except asyncio.CancelledError:
                raise
            except Exception:
                log.warning("Account %s connection dropped", account_id, exc_info=True)
            finally:
                if slot is not None:
                    slot.client = None
                await _safe_stop(client)
                set_account_running(account_id, False)
            if invalid is not None:
                await self._invalidate(account_id, details, invalid)
                return
            if stop_event.is_set() or self._shutdown.is_set():
                return
            log.info("Account %s disconnected. Reconnecting in %.1fs.", account_id, delay)
            await self._interruptible_sleep(delay, stop_event)
            delay = min(delay * 2, self.backoff_max)

    async def _serve(
        self,
        account_id: int,
        client: Any,
        details: dict[str, Any],
        stop_event: asyncio.Event,
        refresh: asyncio.Event,
    ) -> None:
        limiter = self._limiter_for(account_id)
        session = AccountSession(
            account_id=account_id,
            client=client,
            details=details,
            limiter=limiter,
            stop_event=stop_event,
            refresh=refresh,
        )
        dispatcher = Dispatcher(self._plugins)
        tasks: list[asyncio.Task[None]] = []

        async def _on_command(_kurigram: Any, message: Any) -> None:
            await self._dispatch(dispatcher, account_id, client, message, limiter)

        handler = MessageHandler(_on_command, _COMMAND_FILTER)
        client.add_handler(handler, COMMAND_HANDLER_GROUP)
        for plugin in self._plugins_for_account():
            spawned = plugin.spawn(session)
            if spawned is None:
                continue
            tasks.append(
                asyncio.create_task(
                    _guard_plugin(plugin.meta.name, spawned),
                    name=f"plugin-{plugin.meta.name}-{account_id}",
                )
            )
        try:
            while not stop_event.is_set() and not self._shutdown.is_set():
                if not getattr(client, "is_connected", False):
                    return
                await self._probe(client, limiter)
                try:
                    await asyncio.wait_for(stop_event.wait(), timeout=self.health_interval)
                    return
                except TimeoutError:
                    continue
        finally:
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            remove = getattr(client, "remove_handler", None)
            if remove is not None:
                try:
                    remove(handler, COMMAND_HANDLER_GROUP)
                except Exception:
                    log.debug("Could not remove the command handler", exc_info=True)

    async def _dispatch(
        self,
        dispatcher: Dispatcher,
        account_id: int,
        client: Any,
        message: Any,
        limiter: AccountLimiter,
    ) -> None:
        try:
            from src.database import get_user_language

            owner_id = self._owner_id(account_id)
            language = get_user_language(owner_id) if owner_id else "en"
            await dispatcher.handle_message(
                account_id=account_id,
                client=client,
                message=message,
                limiter=limiter,
                language=language,
            )
        except Exception:
            log.exception("Command dispatch failed for account %s", account_id)

    def _owner_id(self, account_id: int) -> int | None:
        slot_details = None
        # The candidate row is not stored on the slot. Read the owner from the DB.
        from src.runtime.store import get_plugin_account

        account = get_plugin_account(account_id)
        if account is None:
            return None
        slot_details = account.get("telegram_id")
        return int(slot_details) if slot_details is not None else None

    async def _probe(self, client: Any, limiter: AccountLimiter) -> None:
        get_me = getattr(client, "get_me", None)
        if get_me is None:
            return
        try:
            await limiter.run(get_me)
        except FloodDeferred:
            return
        except Exception as exc:
            if _is_auth_error(exc):
                raise SessionInvalid(str(exc)) from exc
            log.warning("Health probe failed: %s", exc)
            raise ConnectFailed(str(exc)) from exc

    async def _invalidate(
        self,
        account_id: int,
        details: dict[str, Any],
        exc: BaseException,
    ) -> None:
        log.warning("Account %s session is invalid: %s", account_id, exc)
        from src.database import mark_session_invalid

        mark_session_invalid(account_id, str(exc))
        set_account_running(account_id, False)
        owner_id = details.get("telegram_id")
        if not owner_id:
            return
        from src.translation import get_translation_func_for_user

        _ = get_translation_func_for_user(int(owner_id))
        phone = escape(str(details.get("phone") or ""))
        text = _(
            "⚠️ The session for <code>{phone}</code> is invalid or revoked. "
            "The account was stopped. Delete it and add it again."
        ).format(phone=phone)
        try:
            await self.notifier.send(int(owner_id), text)
        except Exception:
            log.exception("Failed to notify owner %s about account %s", owner_id, account_id)

    async def _interruptible_sleep(self, seconds: float, stop_event: asyncio.Event) -> None:
        if seconds <= 0 or stop_event.is_set() or self._shutdown.is_set():
            return
        sleep_task: asyncio.Task[None] = asyncio.create_task(self.sleeper(seconds))
        stop_task = asyncio.create_task(stop_event.wait())
        shutdown_task = asyncio.create_task(self._shutdown.wait())
        try:
            done, _pending = await asyncio.wait(
                {sleep_task, stop_task, shutdown_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
        finally:
            for task in (sleep_task, stop_task, shutdown_task):
                if not task.done():
                    task.cancel()

    async def _listen_postgres(self) -> None:
        dsn = _asyncpg_dsn(current_database_url())
        if dsn is None:
            return
        import asyncpg

        while not self._shutdown.is_set():
            connection = None
            try:
                connection = await asyncpg.connect(dsn)

                def _on_notify(*_args: object) -> None:
                    self._wake.set()

                await connection.add_listener(CHANNEL, _on_notify)
                while not self._shutdown.is_set():
                    await asyncio.sleep(1)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.warning("Postgres LISTEN failed; polling will continue", exc_info=True)
                try:
                    await asyncio.wait_for(self._shutdown.wait(), timeout=5)
                except TimeoutError:
                    continue
            finally:
                if connection is not None:
                    await connection.close()


def _record_flood(account_id: int, seconds: int) -> None:
    from src.database import set_account_flood_wait

    until = datetime.now(UTC).replace(tzinfo=None) + timedelta(seconds=seconds)
    set_account_flood_wait(account_id, until)


def _mark_session_ok(account_id: int) -> None:
    from src.database import mark_session_ok

    mark_session_ok(account_id)


async def _guard_plugin(name: str, spawned: Awaitable[None]) -> None:
    try:
        await spawned
    except asyncio.CancelledError:
        raise
    except Exception:
        log.exception("Plugin %s crashed and was isolated", name)


async def _safe_stop(client: Any) -> None:
    try:
        if getattr(client, "is_connected", False) or getattr(client, "is_initialized", False):
            await client.stop()
    except Exception:
        log.debug("Error stopping runtime client", exc_info=True)


async def start_embedded_supervisor(bot: Any) -> Supervisor:
    """Run the runtime beside the control bot when ``RUNTIME_ROLE=all``."""
    supervisor = Supervisor(notifier=BotNotifier(bot))
    await supervisor.start()
    return supervisor


async def stop_embedded_supervisor() -> None:
    current = _CURRENT
    if current is not None:
        await current.stop()
