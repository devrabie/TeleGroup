import logging
import requests
from telegram.ext import ContextTypes

from src import config
from src.database import batch_insert_proxies

log = logging.getLogger(__name__)

async def update_proxies_from_url(context: ContextTypes.DEFAULT_TYPE):
    """
    Downloads the proxy list from the configured URL and updates the database.
    """
    url = config.WEBSHARE_PROXY_API_URL
    if not url:
        log.warning("WEBSHARE_PROXY_API_URL is not set. Skipping proxy download.")
        return

    log.info(f"Attempting to download proxies from the configured URL...")

    try:
        response = requests.get(url, timeout=20)
        response.raise_for_status()  # Raise an exception for bad status codes (4xx or 5xx)

        # The response text is expected to be a list of proxies, one per line.
        proxy_list = response.text.strip().splitlines()

        if not proxy_list or (len(proxy_list) == 1 and not proxy_list[0]):
            log.warning("Downloaded proxy list is empty.")
            return

        new_proxies_count = batch_insert_proxies(proxy_list)
        log.info(f"Proxy update complete. Added {new_proxies_count} new proxies to the database.")

    except requests.exceptions.RequestException as e:
        log.error(f"Failed to download proxy list due to a network error: {e}")
    except Exception as e:
        log.error(f"An unexpected error occurred during proxy update: {e}")
