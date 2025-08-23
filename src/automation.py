import asyncio
import logging
import random
from telegram.ext import ContextTypes
from pyrogram import Client
from pyrogram.errors import FloodWait

from src.database import (
    get_eligible_accounts,
    get_groups_created_today,
    get_account_stats,
    get_proxy_string,
    log_group_creation,
    mark_proxy_as_bad,
    reassign_proxy,
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


async def process_single_account(account_details: dict):
    """Handles the group creation for a single managed account."""
    account_id = account_details['account_id']
    session_string = account_details['session_string']
    proxy_id = account_details['proxy_id']

    proxy_string = get_proxy_string(proxy_id)
    proxy_dict = None
    if proxy_string:
        try:
            # Assuming format: hostname:port:username:password
            hostname, port, username, password = proxy_string.split(':')
            proxy_dict = {
                "scheme": "http",
                "hostname": hostname,
                "port": int(port),
                "username": username,
                "password": password,
            }
        except (ValueError, IndexError) as e:
            log.error(f"Invalid proxy format for account {account_id}: '{proxy_string}'. Error: {e}")
            # Optionally, mark proxy as bad here
            return

    # Use a unique name for the client to avoid conflicts
    client_name = f"auto_session_{account_id}_{random.randint(1000, 9999)}"
    user_client = Client(client_name, session_string=session_string, in_memory=True, proxy=proxy_dict)

    try:
        await user_client.start()
        log.info(f"Successfully started client for account {account_id}.")

        total_groups_created = get_account_stats(account_id)
        new_group_name = str(total_groups_created + 1)

        new_group = await user_client.create_group(title=new_group_name, users=[]) # Create empty group
        log.info(f"Account {account_id} created group '{new_group_name}' (ID: {new_group.id}).")

        # Convert to supergroup and send message
        await asyncio.sleep(random.uniform(2, 5)) # Small delay before next action
        await user_client.set_chat_history_for_new_members_enabled(new_group.id, True)
        await asyncio.sleep(random.uniform(2, 5))
        await user_client.send_message(new_group.id, f"Hello, group {new_group_name} is ready.")

        # Log the success
        log_group_creation(account_id, new_group.id, new_group_name)
        log.info(f"Successfully processed and logged group creation for account {account_id}.")

    except FloodWait as e:
        log.warning(f"Account {account_id} is flood-waited for {e.value} seconds. Skipping for now.")
        # A more advanced system could mark the account as "resting" in the DB.
    except Exception as e:
        log.error(f"An unexpected error occurred while processing account {account_id}: {e}", exc_info=True)

        # Assume the error might be proxy-related. Mark old proxy as bad and assign a new one.
        if proxy_id:
            log.warning(f"Attempting to rotate proxy for account {account_id} due to error.")
            mark_proxy_as_bad(proxy_id)
            owner_telegram_id = account_details['telegram_id']
            success, msg = reassign_proxy(account_id, owner_telegram_id)
            if success:
                log.info(f"Successfully reassigned a new proxy to account {account_id}.")
            else:
                log.error(f"Failed to reassign proxy for account {account_id}: {msg}")
    finally:
        if user_client.is_connected:
            await user_client.stop()
        log.debug(f"Client for account {account_id} stopped.")
