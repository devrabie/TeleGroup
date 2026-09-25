"""Environment defaults so importing the bot does not require a developer .env."""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("BOT_TOKEN", "1:test")
os.environ.setdefault("API_ID", "1")
os.environ.setdefault("API_HASH", "hash")
os.environ.setdefault("ADMIN_IDS", "1")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:////tmp/telegroup-pytest.db")
os.environ.setdefault("SESSION_ENCRYPTION_KEY", "qoaGk05XJfuMlnrnND1-suk6JtqXR-Y04CWEJNH5KgU=")
