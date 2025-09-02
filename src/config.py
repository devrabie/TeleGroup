import os
from dotenv import load_dotenv

# Load environment variables from a .env file for local development
load_dotenv()

# --- Telegram Bot Configuration ---
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("Missing required environment variable: BOT_TOKEN")

# --- Pyrogram Client Configuration ---
API_ID = os.getenv("API_ID")
API_HASH = os.getenv("API_HASH")
if not all([API_ID, API_HASH]):
    raise ValueError("Missing required environment variables: API_ID and/or API_HASH")

# This is now optional. It's only required for non-Star payments.
PAYMENT_PROVIDER_TOKEN = os.getenv("PAYMENT_PROVIDER_TOKEN")

# --- Bot Administration ---
# The Telegram user ID of the bot administrator.
# Can be a comma-separated list of IDs for multiple admins.
ADMIN_IDS_STR = os.getenv("ADMIN_IDS", "")
ADMIN_IDS = [int(admin_id.strip()) for admin_id in ADMIN_IDS_STR.split(',') if admin_id.strip()]
if not ADMIN_IDS:
    raise ValueError("Missing required environment variable: ADMIN_IDS")

# --- Proxy Configuration ---
WEBSHARE_PROXY_API_URL = os.getenv(
    "WEBSHARE_PROXY_API_URL",
    "https://proxy.webshare.io/api/v2/proxy/list/download/uaykgtjmscislovqzscyrzsooiglcnagpsovmqjy/-/any/username/direct/-/"
)

# --- Data Files ---
DATA_PROXIES_FILE = "data/proxies.txt"

# --- Display Timezone ---
# The timezone to use for displaying dates and times to the user.
# Should be a valid IANA timezone name (e.g., "Asia/Riyadh", "Europe/London").
# Defaults to "UTC" if not set.
DISPLAY_TIMEZONE = os.getenv("DISPLAY_TIMEZONE", "UTC")


# --- Logging ---
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
