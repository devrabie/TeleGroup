"""In-process cancellation for long userbot jobs.

One job of each kind can run per account. The worker owns the client, so the
flag does not need to live in the database. Restarting the worker stops the job.
"""

from __future__ import annotations

import asyncio

_jobs: dict[tuple[int, str], asyncio.Event] = {}


def begin_job(account_id: int, kind: str) -> asyncio.Event | None:
    key = (account_id, kind)
    if key in _jobs:
        return None
    event = asyncio.Event()
    _jobs[key] = event
    return event


def cancel_job(account_id: int, kind: str) -> bool:
    event = _jobs.get((account_id, kind))
    if event is None:
        return False
    event.set()
    return True


def finish_job(account_id: int, kind: str) -> None:
    _jobs.pop((account_id, kind), None)


def job_running(account_id: int, kind: str) -> bool:
    return (account_id, kind) in _jobs
