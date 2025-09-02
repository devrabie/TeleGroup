import asyncio
import logging
import random
from datetime import datetime, timedelta
from telegram.ext import ContextTypes
from pyrogram import Client
from pyrogram.errors import FloodWait, Timeout

from src.database import (
    get_eligible_accounts,
    get_groups_created_today,
    get_account_stats,
    get_proxy_string,
    get_random_proxy_id,
    log_group_creation,
    mark_proxy_as_bad,
    reassign_proxy,
    set_account_flood_wait,
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

        # Check daily limit
        created_today = get_groups_created_today(account_id)
        if created_today >= account['daily_group_limit']:
            log.info(f"Account {account_id} has reached its daily limit of {account['daily_group_limit']} groups.")
            continue

        try:
            # Add a random delay to avoid all accounts acting at once
            delay = random.uniform(10, 60)
            log.debug(f"Waiting for {delay:.2f}s before processing account {account_id}.")
            await asyncio.sleep(delay)

            await process_single_account(account)

        except Exception as e:
            log.error(f"An unexpected error occurred in the main loop for account {account_id}: {e}", exc_info=True)

    log.info("Automation cycle finished.")


MAX_PROXY_RETRIES = 3

async def process_single_account(account_details: dict):
    """
    Handles the group creation for a single managed account.
    Retries with a new proxy if the connection fails.
    """
    account_id = account_details['account_id']
    session_string = account_details['session_string']

    for attempt in range(MAX_PROXY_RETRIES):
        user_client = None
        proxy_id = get_random_proxy_id()  # Get a new random proxy for each attempt
        proxy_string = get_proxy_string(proxy_id) if proxy_id else None
        proxy_dict = None

        if proxy_string:
            try:
                hostname, port, username, password = proxy_string.split(':')
                proxy_dict = {"scheme": "socks5", "hostname": hostname, "port": int(port), "username": username, "password": password}
                log.info(f"Account {account_id} | Attempt {attempt + 1}/{MAX_PROXY_RETRIES}: Using proxy {hostname}")
            except (ValueError, IndexError) as e:
                log.error(f"Invalid proxy format for account {account_id}: '{proxy_string}'. Error: {e}")
                if proxy_id: mark_proxy_as_bad(proxy_id)
                continue  # Try with another proxy
        else:
            log.warning(f"Account {account_id} | Attempt {attempt + 1}/{MAX_PROXY_RETRIES}: No proxy available. Proceeding without proxy.")

        try:
            client_name = f"auto_session_{account_id}_{random.randint(1000, 9999)}"
            user_client = Client(client_name, session_string=session_string, in_memory=True, proxy=proxy_dict)

            await user_client.start()
            log.info(f"Successfully started client for account {account_id}.")

            total_groups_created = get_account_stats(account_id)
            now = datetime.now()
            date_str = now.strftime("%Y-%m")
            new_group_name = f"Group {total_groups_created + 1} {date_str}"

            new_group = await user_client.create_supergroup(title=new_group_name, description="")
            log.info(f"Account {account_id} created supergroup '{new_group_name}' (ID: {new_group.id}).")
            log_group_creation(account_id, new_group.id, new_group_name)

            await asyncio.sleep(random.uniform(2, 5))
            await user_client.send_message(new_group.id, f"Hello, group {new_group_name} is ready.")

            log.info(f"Successfully processed group creation for account {account_id}.")
            return  # Exit the loop on success

        except (Timeout, ConnectionError) as e:
            log.warning(f"Connection failed for account {account_id} on attempt {attempt + 1}/{MAX_PROXY_RETRIES}. Proxy ID: {proxy_id}. Error: {e}")
            if proxy_id: mark_proxy_as_bad(proxy_id)
            if attempt >= MAX_PROXY_RETRIES - 1:
                log.error(f"Account {account_id} failed to connect after {MAX_PROXY_RETRIES} attempts. Skipping for this cycle.")
                break # Break loop after final attempt
            await asyncio.sleep(1) # Wait before next attempt

        except FloodWait as e:
            log.warning(f"Account {account_id} is flood-waited for {e.value} seconds. Storing wait time in DB.")
            wait_until = datetime.now() + timedelta(seconds=e.value)
            set_account_flood_wait(account_id, wait_until)
            break  # Don't retry on FloodWait, just wait for the next cycle

        except Exception as e:
            log.error(f"An unexpected error occurred while processing account {account_id}: {e}", exc_info=True)
            break # Break loop on other unexpected errors

        finally:
            if user_client and user_client.is_connected:
                await user_client.stop()
            log.debug(f"Client for account {account_id} stopped for attempt {attempt + 1}.")
