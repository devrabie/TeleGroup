"""Bounded child processes for downloads and media conversion.

At most ``media_workers`` jobs run at once (default 1, hard maximum 2). Each
job is a fresh process so yt-dlp and ffmpeg are not imported into the account
event loop, and the process is gone when the job ends. Cancelling the event
terminates that process.
"""

from __future__ import annotations

import asyncio
import multiprocessing as mp
import time
from typing import Any

_global: asyncio.Semaphore | None = None
_loop: asyncio.AbstractEventLoop | None = None
_accounts: dict[int, tuple[int, asyncio.Semaphore]] = {}


def _workers() -> int:
    from src.config import get_settings

    return max(1, min(int(get_settings().media_workers), 2))


def _global_limit() -> asyncio.Semaphore:
    global _global, _loop
    loop = asyncio.get_running_loop()
    if _global is None or _loop is not loop:
        _global = asyncio.Semaphore(_workers())
        _loop = loop
        _accounts.clear()
    return _global


def _account_limit(account_id: int, slots: int) -> asyncio.Semaphore:
    slots = max(1, min(int(slots), 2))
    current = _accounts.get(account_id)
    if current is None or current[0] != slots:
        created = asyncio.Semaphore(slots)
        _accounts[account_id] = (slots, created)
        return created
    return current[1]


async def _acquire(sem: asyncio.Semaphore, cancel: asyncio.Event) -> bool:
    while not cancel.is_set():
        try:
            await asyncio.wait_for(sem.acquire(), timeout=0.4)
        except TimeoutError:
            continue
        return True
    return False


def _run_process(name: str, payload: dict[str, Any], cancel: asyncio.Event) -> dict[str, Any]:
    ctx = mp.get_context("spawn")
    queue: Any = ctx.Queue()
    proc = ctx.Process(target=_child_target(), args=(name, payload, queue), daemon=True)
    proc.start()
    try:
        while proc.is_alive():
            if cancel.is_set():
                proc.terminate()
                proc.join(3)
                if proc.is_alive():
                    proc.kill()
                    proc.join(1)
                return {"ok": False, "error": "cancelled"}
            proc.join(0.4)
        deadline = time.monotonic() + 2
        while queue.empty() and time.monotonic() < deadline:
            time.sleep(0.05)
        if queue.empty():
            return {"ok": False, "error": "failed"}
        result = queue.get_nowait()
        if isinstance(result, dict):
            return result
        return {"ok": False, "error": "failed"}
    finally:
        if proc.is_alive():
            proc.terminate()
            proc.join(2)
        queue.close()
        queue.cancel_join_thread()


def _child_target() -> Any:
    from src.media_jobs import child_main

    return child_main


async def run_named(
    account_id: int,
    name: str,
    payload: dict[str, Any],
    *,
    slots: int = 1,
    cancel: asyncio.Event | None = None,
) -> dict[str, Any]:
    """Run ``name`` from ``src.media_jobs.JOBS`` outside the event loop."""
    token = cancel or asyncio.Event()
    account = _account_limit(account_id, slots)
    if not await _acquire(account, token):
        return {"ok": False, "error": "cancelled"}
    try:
        if not await _acquire(_global_limit(), token):
            return {"ok": False, "error": "cancelled"}
        try:
            return await asyncio.to_thread(_run_process, name, payload, token)
        finally:
            _global_limit().release()
    finally:
        account.release()
