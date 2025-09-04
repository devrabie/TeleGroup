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

# --- Crypto Pay API Configuration ---
CRYPTO_PAY_API_TOKEN = os.getenv("CRYPTO_PAY_API_TOKEN")
CRYPTO_PAY_API_BASE_URL = os.getenv("CRYPTO_PAY_API_BASE_URL", "https://pay.crypt.bot/api/")

# --- Webhook Configuration (Optional) ---
# Required for receiving Crypto Pay updates.
WEBHOOK_ENABLED = os.getenv("WEBHOOK_ENABLED", "false").lower() in ('true', '1', 't')
WEBHOOK_URL = os.getenv("WEBHOOK_URL") # e.g., https://your-domain.com
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET") # A random secret string
WEBHOOK_LISTEN_ADDRESS = os.getenv("WEBHOOK_LISTEN_ADDRESS", "0.0.0.0")
WEBHOOK_PORT = int(os.getenv("WEBHOOK_PORT", "8443"))


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
# Optional override for proxy credentials. If set, these will be used for all proxies.
PROXY_USERNAME = os.getenv("PROXY_USERNAME")
PROXY_PASSWORD = os.getenv("PROXY_PASSWORD")

# --- Data Files ---
DATA_PROXIES_FILE = "data/proxies.txt"

# --- Display Timezone ---
# The timezone to use for displaying dates and times to the user.
# Should be a valid IANA timezone name (e.g., "Asia/Riyadh", "Europe/London").
# Defaults to "UTC" if not set.
DISPLAY_TIMEZONE = os.getenv("DISPLAY_TIMEZONE", "UTC")


# --- Logging ---
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
