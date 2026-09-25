"""The only place that constructs Kurigram user clients.

Group creation, code monitoring, and future plugins should call
``build_user_client`` so a later runtime can wrap proxy, device profile,
and session handling in one spot.
"""

from __future__ import annotations

from typing import Any

from pyrogram import Client


def build_user_client(name: str, **kwargs: Any) -> Client:
    """Build an in-memory Kurigram client. Keyword arguments are passed through."""
    return Client(name, **kwargs)
