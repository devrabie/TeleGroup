"""Per-account slots for long userbot jobs.

``jobs.py`` allows one job of a kind. Downloads and conversions can allow a
second slot. The cap is still 1 or 2, and the process pool caps the machine.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

_held: dict[tuple[int, str], list[Slot]] = {}


@dataclass
class Slot:
    cancel: asyncio.Event


def try_acquire(account_id: int, kind: str, limit: int) -> Slot | None:
    limit = max(1, min(int(limit), 2))
    key = (account_id, kind)
    held = _held.setdefault(key, [])
    if len(held) >= limit:
        return None
    slot = Slot(cancel=asyncio.Event())
    held.append(slot)
    return slot


def release(account_id: int, kind: str, slot: Slot) -> None:
    key = (account_id, kind)
    held = _held.get(key)
    if not held:
        return
    _held[key] = [item for item in held if item is not slot]
    if not _held[key]:
        _held.pop(key, None)


def cancel_kind(account_id: int, kind: str) -> int:
    held = list(_held.get((account_id, kind), []))
    for slot in held:
        slot.cancel.set()
    return len(held)
