"""Start one Kurigram client, rotating proxies the same way the code monitor does."""

from __future__ import annotations

import asyncio
import logging
import random
from typing import Any

from src import config
from src.code_monitor import (
    AUTH_ERRORS,
    build_proxy_dict,
    is_socks_auth_error,
)
from src.database import (
    assign_account_proxy,
    get_proxy_string,
    get_random_proxy_id,
    rotate_account_proxy,
)
from src.runtime.client_factory import build_user_client

log = logging.getLogger(__name__)

MAX_PROXY_RETRIES = 3
START_TIMEOUT = 45.0


class SessionInvalid(Exception):
    """The stored session was revoked or rejected by Telegram."""


class ConnectFailed(Exception):
    """Every proxy attempt failed with a connection error."""


async def _safe_stop(client: Any) -> None:
    try:
        if getattr(client, "is_connected", False) or getattr(client, "is_initialized", False):
            await client.stop()
    except Exception:
        log.debug("Failed to stop a client that did not start", exc_info=True)


async def connect_account(details: dict[str, Any]) -> Any:
    """Return a started client or raise ``SessionInvalid`` / ``ConnectFailed``."""
    account_id = int(details["account_id"])
    session_string = details.get("session_string")
    if not session_string:
        raise ConnectFailed(f"Account {account_id} has no session string")

    original_proxy_id = details.get("proxy_id")
    proxy_id = original_proxy_id or get_random_proxy_id()
    last_error: BaseException | None = None

    for attempt in range(MAX_PROXY_RETRIES):
        proxy_string = get_proxy_string(proxy_id) if proxy_id else None
        proxy_dict = build_proxy_dict(proxy_string) if proxy_string else None
        if proxy_string and proxy_dict is None:
            proxy_id = rotate_account_proxy(account_id, proxy_id)
            continue

        client = build_user_client(
            f"runtime_{account_id}_{random.randint(1000, 9999)}",
            session_string=session_string,
            api_id=details.get("api_id") or config.API_ID,
            api_hash=details.get("api_hash") or config.API_HASH,
            device_model=details.get("device_model"),
            system_version=details.get("system_version"),
            app_version=details.get("app_version"),
            lang_code=details.get("lang_code"),
            in_memory=True,
            no_updates=False,
            proxy=proxy_dict,
        )
        try:
            await asyncio.wait_for(client.start(), timeout=START_TIMEOUT)
        except AUTH_ERRORS as exc:
            await _safe_stop(client)
            raise SessionInvalid(str(exc)) from exc
        except (TimeoutError, ConnectionError, OSError) as exc:
            last_error = exc
            reason = "SOCKS5 authentication failed" if is_socks_auth_error(exc) else str(exc)
            log.warning(
                "Runtime connect failed for account %s (attempt %s/%s, proxy %s): %s",
                account_id,
                attempt + 1,
                MAX_PROXY_RETRIES,
                proxy_id,
                reason,
            )
            await _safe_stop(client)
            proxy_id = rotate_account_proxy(account_id, proxy_id)
            await asyncio.sleep(1)
            continue
        except Exception as exc:
            if is_socks_auth_error(exc):
                last_error = exc
                log.warning(
                    "SOCKS5 authentication failed for account %s (attempt %s/%s, proxy %s)",
                    account_id,
                    attempt + 1,
                    MAX_PROXY_RETRIES,
                    proxy_id,
                )
                await _safe_stop(client)
                proxy_id = rotate_account_proxy(account_id, proxy_id)
                await asyncio.sleep(1)
                continue
            await _safe_stop(client)
            raise ConnectFailed(str(exc)) from exc

        if proxy_id and proxy_id != original_proxy_id:
            assign_account_proxy(account_id, proxy_id)
        log.info("Runtime client connected for account %s", account_id)
        return client

    raise ConnectFailed(str(last_error) if last_error else "connection failed")
