"""Per-account action queue and FloodWait handling.

Telegram bans accounts that fire requests in parallel or ignore FloodWait.
Every userbot action for one account goes through ``AccountLimiter``: one
in-flight call, a minimum gap, and a single retry when the wait is short.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

T = TypeVar("T")


class FloodDeferred(Exception):
    """The call was not retried because Telegram asked us to wait."""

    def __init__(self, seconds: int) -> None:
        self.seconds = seconds
        super().__init__(f"FloodWait {seconds}s")


class ActionQueueFull(Exception):
    """The account already has too many actions waiting."""


def flood_seconds(exc: BaseException) -> int | None:
    """Return the FloodWait delay, or None for any other error."""
    if type(exc).__name__ != "FloodWait":
        return None
    raw = getattr(exc, "value", 0)
    try:
        return max(0, int(raw))
    except (TypeError, ValueError):
        return 0


class AccountLimiter:
    """Serialize Telegram calls for a single managed account."""

    def __init__(
        self,
        account_id: int,
        *,
        min_interval: float = 1.0,
        max_queue: int = 8,
        retry_threshold: int = 30,
        clock: Callable[[], float] | None = None,
        sleeper: Callable[[float], Awaitable[None]] | None = None,
        on_flood: Callable[[int], None] | None = None,
    ) -> None:
        if max_queue < 1:
            raise ValueError("max_queue must be at least 1")
        self.account_id = account_id
        self.min_interval = min_interval
        self.max_queue = max_queue
        self.retry_threshold = retry_threshold
        self._clock = clock or time.monotonic
        self._sleep = sleeper or asyncio.sleep
        self._on_flood = on_flood
        self._gate = asyncio.Lock()
        self._lock = asyncio.Lock()
        self._waiting = 0
        self._next_at = 0.0
        self._flood_until = 0.0

    async def run(self, func: Callable[..., Awaitable[T]], /, *args: Any, **kwargs: Any) -> T:
        async with self._gate:
            if self._waiting >= self.max_queue:
                raise ActionQueueFull(f"Account {self.account_id} action queue is full")
            self._waiting += 1
        try:
            async with self._lock:
                return await self._run_locked(func, args, kwargs)
        finally:
            async with self._gate:
                self._waiting -= 1

    async def _run_locked(
        self,
        func: Callable[..., Awaitable[T]],
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
    ) -> T:
        await self._pace()
        try:
            result = await func(*args, **kwargs)
        except Exception as exc:
            seconds = flood_seconds(exc)
            if seconds is None:
                raise
            self._note_flood(seconds)
            if seconds > self.retry_threshold:
                raise FloodDeferred(seconds) from exc
            await self._sleep(seconds)
            self._flood_until = self._clock()
            try:
                result = await func(*args, **kwargs)
            except Exception as retry_exc:
                retry_seconds = flood_seconds(retry_exc)
                if retry_seconds is None:
                    raise
                self._note_flood(retry_seconds)
                raise FloodDeferred(retry_seconds) from retry_exc
        self._next_at = self._clock() + self.min_interval
        return result

    def _note_flood(self, seconds: int) -> None:
        self._flood_until = max(self._flood_until, self._clock() + seconds)
        if self._on_flood is not None:
            self._on_flood(seconds)

    async def _pace(self) -> None:
        delay = max(self._next_at, self._flood_until) - self._clock()
        if delay > 0:
            await self._sleep(delay)
