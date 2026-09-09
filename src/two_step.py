"""Enable or change Two-Step Verification (cloud password) on a managed account."""

from __future__ import annotations

import asyncio
import logging
import random
from contextlib import asynccontextmanager
from typing import Optional

from pyrogram import Client, raw
from pyrogram.errors import (
    FloodWait,
    PasswordHashInvalid,
    PasswordEmpty,
    PasswordTooFresh,
    Timeout,
)

from src import config
from src.code_monitor import AUTH_ERRORS, build_proxy_dict, get_running_monitor_client, is_socks_auth_error
from src.database import (
    assign_account_proxy,
    get_account_runtime_details,
    get_proxy_string,
    get_random_proxy_id,
    mark_session_invalid,
    mark_session_ok,
    rotate_account_proxy,
)

log = logging.getLogger(__name__)

MIN_TWO_STEP_PASSWORD_LEN = 4
MAX_TWO_STEP_PASSWORD_LEN = 256
MAX_PROXY_RETRIES = 2
CLIENT_START_TIMEOUT = 12.0
OPERATION_TIMEOUT = 12.0

_account_locks: dict[int, asyncio.Lock] = {}


def _account_lock_for(account_id: int) -> asyncio.Lock:
    lock = _account_locks.get(account_id)
    if lock is None:
        lock = asyncio.Lock()
        _account_locks[account_id] = lock
    return lock


class TwoStepError(Exception):
    """User-facing failure while reading or updating 2FA."""

    def __init__(self, code: str, detail: str | None = None):
        super().__init__(code)
        self.code = code
        self.detail = detail


def validate_two_step_password(password: Optional[str]) -> Optional[str]:
    """
    Return an error code if the password is invalid, otherwise None.

    Codes: empty, too_short, too_long, invalid
    """
    if password is None or password == "":
        return "empty"
    if "\n" in password or "\r" in password:
        return "invalid"
    if len(password) < MIN_TWO_STEP_PASSWORD_LEN:
        return "too_short"
    if len(password) > MAX_TWO_STEP_PASSWORD_LEN:
        return "too_long"
    return None


def _map_pyrogram_error(exc: Exception) -> TwoStepError:
    if isinstance(exc, TwoStepError):
        return exc
    if isinstance(exc, PasswordHashInvalid):
        return TwoStepError("wrong_current")
    if isinstance(exc, PasswordEmpty):
        return TwoStepError("empty")
    if isinstance(exc, PasswordTooFresh):
        return TwoStepError("too_fresh")
    if isinstance(exc, FloodWait):
        return TwoStepError("flood_wait", str(getattr(exc, "value", "")))
    if isinstance(exc, AUTH_ERRORS):
        return TwoStepError("session_invalid")
    if isinstance(exc, ValueError):
        message = str(exc).lower()
        if "already a cloud password" in message:
            return TwoStepError("already_enabled")
        if "no cloud password" in message:
            return TwoStepError("not_enabled")
        return TwoStepError("unexpected", str(exc))
    return TwoStepError("unexpected", str(exc))


async def _start_temp_client(details: dict) -> Client:
    session_string = details.get("session_string")
    if not session_string:
        raise TwoStepError("account_unavailable")

    last_error = None
    account_id = details["account_id"]
    original_proxy_id = details.get("proxy_id")
    proxy_id = original_proxy_id or get_random_proxy_id()
    for attempt in range(MAX_PROXY_RETRIES):
        proxy_string = get_proxy_string(proxy_id) if proxy_id else None
        proxy_dict = build_proxy_dict(proxy_string) if proxy_string else None
        if proxy_string and proxy_dict is None:
            proxy_id = rotate_account_proxy(account_id, proxy_id)
            continue

        client = Client(
            f"twostep_{account_id}_{random.randint(1000, 9999)}",
            session_string=session_string,
            api_id=details.get("api_id") or config.API_ID,
            api_hash=details.get("api_hash") or config.API_HASH,
            device_model=details.get("device_model"),
            system_version=details.get("system_version"),
            app_version=details.get("app_version"),
            lang_code=details.get("lang_code"),
            in_memory=True,
            no_updates=True,
            proxy=proxy_dict,
        )
        try:
            await asyncio.wait_for(client.start(), timeout=CLIENT_START_TIMEOUT)
            if proxy_id and proxy_id != original_proxy_id:
                assign_account_proxy(account_id, proxy_id)
            mark_session_ok(account_id)
            return client
        except AUTH_ERRORS as e:
            await _safe_stop(client)
            mark_session_invalid(account_id, str(e))
            raise TwoStepError("session_invalid") from e
        except (asyncio.TimeoutError, Timeout, ConnectionError, OSError) as e:
            last_error = e
            await _safe_stop(client)
            reason = "SOCKS5 authentication failed" if is_socks_auth_error(e) else str(e)
            log.warning(
                f"Two-step client connect failed for account {account_id} "
                f"(attempt {attempt + 1}/{MAX_PROXY_RETRIES}, proxy {proxy_id}): {reason}"
            )
            proxy_id = rotate_account_proxy(account_id, proxy_id)
            await asyncio.sleep(0.5)
        except Exception as e:
            if is_socks_auth_error(e):
                last_error = e
                await _safe_stop(client)
                log.warning(
                    f"SOCKS5 authentication failed for two-step client on account {account_id} "
                    f"(attempt {attempt + 1}/{MAX_PROXY_RETRIES}, proxy {proxy_id})"
                )
                proxy_id = rotate_account_proxy(account_id, proxy_id)
                await asyncio.sleep(0.5)
                continue
            await _safe_stop(client)
            raise _map_pyrogram_error(e) from e

    raise TwoStepError("connect_failed", str(last_error) if last_error else None)


async def _safe_stop(client: Optional[Client]) -> None:
    if client is None:
        return
    try:
        if getattr(client, "is_connected", False) or getattr(client, "is_initialized", False):
            await client.stop()
    except Exception as e:
        log.debug(f"Error stopping two-step client: {e}")


@asynccontextmanager
async def open_account_client(account_id: int):
    """Yield a connected Pyrogram client, reusing the code-monitor session when possible."""
    async with _account_lock_for(account_id):
        running = get_running_monitor_client(account_id)
        if running is not None:
            yield running
            return

        details = get_account_runtime_details(account_id)
        if not details:
            raise TwoStepError("account_unavailable")

        client = await _start_temp_client(details)
        try:
            yield client
        finally:
            await _safe_stop(client)


async def get_two_step_status(account_id: int) -> dict:
    """Return ``has_password`` and ``hint`` for the managed account."""
    try:
        async with open_account_client(account_id) as client:
            result = await asyncio.wait_for(
                client.invoke(raw.functions.account.GetPassword()),
                timeout=OPERATION_TIMEOUT,
            )
            return {
                "has_password": bool(getattr(result, "has_password", False)),
                "hint": getattr(result, "hint", None) or "",
            }
    except TwoStepError:
        raise
    except asyncio.TimeoutError as e:
        raise TwoStepError("connect_failed", "Operation timed out") from e
    except Exception as e:
        log.error(f"Failed to read 2FA status for account {account_id}: {e}", exc_info=True)
        raise _map_pyrogram_error(e) from e


async def apply_two_step_password(
    account_id: int,
    new_password: str,
    current_password: Optional[str] = None,
    hint: str = "",
) -> str:
    """
    Enable or change the cloud password.

    Returns ``enabled`` or ``changed``.
    """
    error = validate_two_step_password(new_password)
    if error:
        raise TwoStepError(error)
    if current_password is not None and current_password == "":
        raise TwoStepError("wrong_current")

    try:
        async with open_account_client(account_id) as client:
            status = await asyncio.wait_for(
                client.invoke(raw.functions.account.GetPassword()),
                timeout=OPERATION_TIMEOUT,
            )
            if getattr(status, "has_password", False):
                if not current_password:
                    raise TwoStepError("current_required")
                await asyncio.wait_for(
                    client.change_cloud_password(
                        current_password, new_password, new_hint=hint or ""
                    ),
                    timeout=OPERATION_TIMEOUT,
                )
                return "changed"
            await asyncio.wait_for(
                client.enable_cloud_password(new_password, hint=hint or ""),
                timeout=OPERATION_TIMEOUT,
            )
            return "enabled"
    except TwoStepError:
        raise
    except asyncio.TimeoutError as e:
        raise TwoStepError("connect_failed", "Operation timed out") from e
    except Exception as e:
        log.error(f"Failed to update 2FA for account {account_id}: {e}", exc_info=True)
        raise _map_pyrogram_error(e) from e
