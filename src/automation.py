import asyncio
import logging
import random
from datetime import datetime
from telegram.ext import ContextTypes
from pyrogram import Client
from pyrogram.errors import FloodWait, Timeout

from src import config
from src.code_monitor import AUTH_ERRORS, build_proxy_dict, get_running_monitor_client, is_socks_auth_error
from src.database import (
    get_eligible_accounts,
    get_account_stats,
    get_proxy_string,
    get_random_proxy_id,
    log_group_creation,
    mark_proxy_as_bad,
    mark_session_invalid,
    mark_session_ok,
    update_account_schedule,
    apply_error_backoff,
)

log = logging.getLogger(__name__)

async def run_group_creation_cycle(context: ContextTypes.DEFAULT_TYPE):
    """The main automation cycle that creates groups."""
    log.info("Automation cycle started.")
    eligible_accounts = get_eligible_accounts()

    if not eligible_accounts:
        log.info("No eligible accounts to process in this cycle.")
        return

    log.info(f"Found {len(eligible_accounts)} eligible accounts to process.")

    for account in eligible_accounts:
        account_id = account['account_id']

        try:
            # Add a random delay to avoid all accounts acting at once
            delay = random.uniform(5, 20)
            log.debug(f"Waiting for {delay:.2f}s before processing account {account_id}.")
            await asyncio.sleep(delay)

            await process_single_account(account)

        except Exception as e:
            log.error(f"An unexpected error occurred in the main loop for account {account_id}: {e}", exc_info=True)

    log.info("Automation cycle finished.")


MAX_PROXY_RETRIES = 3


async def _create_group_on_client(user_client: Client, account_details: dict) -> None:
    """Create one supergroup on an already-started client. Does not stop the client."""
    account_id = account_details['account_id']
    total_groups_created = get_account_stats(account_id)
    now = datetime.now()
    date_str = now.strftime("%Y-%m")
    new_group_name = f"Group {total_groups_created + 1} {date_str}"

    new_group = await asyncio.wait_for(
        user_client.create_supergroup(title=new_group_name, description=""),
        timeout=30.0
    )
    log.info(f"Account {account_id} created supergroup '{new_group_name}' (ID: {new_group.id}).")

    log_group_creation(account_id, new_group.id, new_group_name)
    update_account_schedule(account_id, account_details['daily_group_limit'])

    await user_client.send_message(new_group.id, f"Hello, group {new_group_name} is ready.")
    log.info(f"Successfully processed group creation for account {account_id}.")


async def process_single_account(account_details: dict):
    """
    Handles the group creation for a single managed account.
    Reuses a running code-monitor client when available to avoid session conflicts.
    Otherwise retries with a new proxy if the connection fails.
    """
    account_id = account_details['account_id']
    session_string = account_details['session_string']

    monitor_client = get_running_monitor_client(account_id)
    if monitor_client is not None:
        try:
            await _create_group_on_client(monitor_client, account_details)
            return
        except FloodWait as e:
            log.warning(f"Account {account_id} is flood-waited for {e.value} seconds. Applying backoff.")
            apply_error_backoff(account_id, str(e), e.value)
            return
        except (asyncio.TimeoutError, Timeout, ConnectionError) as e:
            log.warning(f"Timeout/connection error using monitor client for account {account_id}: {e}")
            apply_error_backoff(account_id, f"Group creation failed on monitor client: {e}")
            return
        except Exception as e:
            log.error(f"Group creation failed on monitor client for account {account_id}: {e}", exc_info=True)
            apply_error_backoff(account_id, str(e))
            return

    for attempt in range(MAX_PROXY_RETRIES):
        user_client = None
        proxy_id = get_random_proxy_id()  # Get a new random proxy for each attempt
        proxy_string = get_proxy_string(proxy_id) if proxy_id else None
        proxy_dict = None

        if proxy_string:
            proxy_dict = build_proxy_dict(proxy_string)
            if proxy_dict is None:
                if proxy_id:
                    mark_proxy_as_bad(proxy_id)
                continue
            log.info(f"Account {account_id} | Attempt {attempt + 1}/{MAX_PROXY_RETRIES}: Using proxy {proxy_dict['hostname']}")
        else:
            log.warning(f"Account {account_id} | Attempt {attempt + 1}/{MAX_PROXY_RETRIES}: No proxy available. Proceeding without proxy.")

        try:
            client_name = f"auto_session_{account_id}_{random.randint(1000, 9999)}"
            user_client = Client(
                client_name,
                session_string=session_string,
                api_id=account_details.get('api_id') or config.API_ID,
                api_hash=account_details.get('api_hash') or config.API_HASH,
                device_model=account_details.get('device_model'),
                system_version=account_details.get('system_version'),
                app_version=account_details.get('app_version'),
                lang_code=account_details.get('lang_code'),
                in_memory=True,
                no_updates=True,
                proxy=proxy_dict
            )

            await asyncio.wait_for(user_client.start(), timeout=30.0)
            log.info(f"Successfully started client for account {account_id}.")
            mark_session_ok(account_id)
            await _create_group_on_client(user_client, account_details)
            return  # Exit the loop on success

        except AUTH_ERRORS as e:
            log.error(f"Session invalid for account {account_id} during group creation: {e}")
            mark_session_invalid(account_id, str(e))
            apply_error_backoff(account_id, str(e))
            break

        except (asyncio.TimeoutError, Timeout, ConnectionError, OSError) as e:
            reason = "SOCKS5 authentication failed" if is_socks_auth_error(e) else type(e).__name__
            log.warning(f"Connection/Timeout error for account {account_id} on attempt {attempt + 1}/{MAX_PROXY_RETRIES}. Proxy ID: {proxy_id}. Error: {reason}")
            if proxy_id:
                mark_proxy_as_bad(proxy_id)
            if attempt >= MAX_PROXY_RETRIES - 1:
                log.error(f"Account {account_id} failed to connect after {MAX_PROXY_RETRIES} attempts. Applying backoff.")
                apply_error_backoff(account_id, f"Connection/Timeout failed after {MAX_PROXY_RETRIES} attempts: {e}")
                break
            await asyncio.sleep(1)

        except FloodWait as e:
            log.warning(f"Account {account_id} is flood-waited for {e.value} seconds. Applying backoff.")
            apply_error_backoff(account_id, str(e), e.value)
            break

        except Exception as e:
            if is_socks_auth_error(e):
                log.warning(
                    f"SOCKS5 authentication failed for account {account_id} "
                    f"on attempt {attempt + 1}/{MAX_PROXY_RETRIES}. Proxy ID: {proxy_id}."
                )
                if proxy_id:
                    mark_proxy_as_bad(proxy_id)
                if attempt >= MAX_PROXY_RETRIES - 1:
                    apply_error_backoff(account_id, f"SOCKS5 authentication failed after {MAX_PROXY_RETRIES} attempts")
                    break
                await asyncio.sleep(1)
                continue
            log.error(f"An unexpected error occurred while processing account {account_id}: {e}", exc_info=True)
            apply_error_backoff(account_id, str(e))
            break

        finally:
            if user_client and user_client.is_connected:
                await user_client.stop()
            log.debug(f"Client for account {account_id} stopped for attempt {attempt + 1}.")
