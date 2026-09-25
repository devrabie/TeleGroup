"""Post the control panel through the control bot's inline mode."""

from __future__ import annotations

import logging
from typing import Any

from src.panel import pack_query
from src.plugins.common import chat_id_of
from src.runtime.plugins import CommandContext

log = logging.getLogger(__name__)

_bot_username: str | None = None


def clear_bot_username_cache() -> None:
    global _bot_username
    _bot_username = None


def is_inline_disabled(exc: BaseException) -> bool:
    name = type(exc).__name__.casefold()
    text = str(exc).casefold().replace(" ", "_")
    if "inline" in name and "disabled" in name:
        return True
    return "bot_inline_disabled" in text or "inline_disabled" in text


async def control_bot_username() -> str:
    global _bot_username
    if _bot_username:
        return _bot_username
    import httpx

    from src.config import get_settings

    token = get_settings().bot_token
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.get(f"https://api.telegram.org/bot{token}/getMe")
        response.raise_for_status()
        payload = response.json()
    username = str(payload["result"]["username"]).lstrip("@")
    _bot_username = username
    return username


async def _account_user_id(ctx: CommandContext) -> int | None:
    me = getattr(ctx.client, "me", None)
    user_id = getattr(me, "id", None)
    if user_id is not None:
        return int(user_id)
    get_me = getattr(ctx.client, "get_me", None)
    if get_me is None:
        return None
    try:
        me = await ctx.limiter.run(get_me)
    except Exception:
        log.debug("Could not read the account user id", exc_info=True)
        return None
    user_id = getattr(me, "id", None)
    if user_id is None:
        return None
    return int(user_id)


async def open_panel(ctx: CommandContext) -> str:
    """Return ``opened``, ``disabled``, or ``failed``."""
    getter = getattr(ctx.client, "get_inline_bot_results", None)
    sender = getattr(ctx.client, "send_inline_bot_result", None)
    if getter is None or sender is None:
        return "failed"
    account_user_id = await _account_user_id(ctx)
    if account_user_id is None:
        return "failed"
    try:
        username = await control_bot_username()
        query = pack_query(ctx.account_id, account_user_id)
        results = await ctx.limiter.run(getter, username, query)
    except Exception as exc:
        if is_inline_disabled(exc):
            return "disabled"
        log.info("Inline panel query failed for account %s", ctx.account_id, exc_info=True)
        return "failed"
    found, query_id, result_id = _first_result(results)
    if found is None or query_id is None or result_id is None:
        return "failed"
    chat_id = chat_id_of(ctx.message) or "me"
    try:
        await ctx.limiter.run(sender, chat_id, query_id, result_id)
    except Exception as exc:
        if is_inline_disabled(exc):
            return "disabled"
        log.info("Sending the inline panel failed for account %s", ctx.account_id, exc_info=True)
        return "failed"
    return "opened"


def _first_result(results: Any) -> tuple[Any, Any, Any]:
    found = getattr(results, "results", None)
    query_id = getattr(results, "query_id", None)
    if found is None and isinstance(results, (list, tuple)) and results:
        found = results
        query_id = getattr(results[0], "query_id", None)
    if not found:
        return None, None, None
    first = found[0]
    if query_id is None:
        query_id = getattr(first, "query_id", None)
    return first, query_id, getattr(first, "id", None)
