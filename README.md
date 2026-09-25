# Telegram Group Creation Bot / بوت إنشاء مجموعات تيليجرام

Multi-account Telegram control bot. The interface uses `python-telegram-bot`. Managed accounts use Kurigram (imported as `pyrogram`). PostgreSQL is the application database.

بوت تحكم متعدد الحسابات. الواجهة عبر `python-telegram-bot`، والحسابات المُدارة عبر Kurigram. قاعدة التشغيل هي PostgreSQL.

## Features / الميزات

- Plans, Telegram Stars, and Crypto Pay subscriptions
- Admin panel, info pages, and English/Arabic UI
- Add account with device profiles, CAPTCHA/login retry handling, and login timeouts
- My Accounts: group creation (off by default), code monitor, 2FA, soft-delete, session status
- Profile, gifts, and private-chat browsing
- Team managers, invite links, and account transfer
- Session explorer, proxy rotation with per-proxy SOCKS5 credentials
- Scheduled supergroup creation, flood-wait backoff, and proxy health checks

الخطط والاشتراك بنجوم تيليجرام وCrypto Pay، لوحة الإدارة، صفحات المعلومات، العربية والإنجليزية، إضافة الحساب مع ملفات الجهاز وإعادة محاولة تسجيل الدخول، مراقبة أكواد الدخول، التحقق بخطوتين، الحذف المنطقي، تصفح الملف والهدايا والمحادثات الخاصة، مديرو الفريق وروابط الدعوة ونقل الحساب، تدوير البروكسي، وإنشاء المجموعات المجدولة.

Phase 2 (not in this change) can add a per-account plugin runtime. Kurigram clients are constructed only in `src/runtime/client_factory.py`.

## Bug status on this branch / حالة الأخطاء

| Report | Status |
| --- | --- |
| Arabic catalogs never load because `.mo` files are gitignored | Already fixed on the base branch. `compile_translations()` runs at startup, and the Docker image compiles catalogs during build. |
| Add-account advances to the code step even when sending the login code fails | Already fixed. `receive_phone_number` waits for `send_login_code` and stays on the phone step when sending fails. |
| Stars `precheckout_callback` approves every payment | Fixed here. Payload, payer, plan, currency (`XTR`), and star amount are checked again before a subscription is granted. |
| Webshare proxy URL with an API token hardcoded as the default | Fixed here. The URL is empty unless `WEBSHARE_PROXY_API_URL` is set. A revoked embedded token is rejected. |

## Setup / الإعداد

```bash
python3 -m venv venv
source venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
```

Required environment variables:

- `BOT_TOKEN`, `API_ID`, `API_HASH`, `ADMIN_IDS`
- `DATABASE_URL` — `postgresql+asyncpg://user:pass@host:5432/telegroup`
- `SESSION_ENCRYPTION_KEY` — Fernet key:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Optional: `WEBSHARE_PROXY_API_URL`, `PROXY_USERNAME`, `PROXY_PASSWORD`, `CRYPTO_PAY_API_TOKEN`, `REDIS_URL`, webhook settings, `DISPLAY_TIMEZONE`, `LOG_LEVEL`.

Create the schema, then start the bot:

```bash
alembic upgrade head
python -m src.main
```

Startup also creates any missing tables, seeds device profiles and default info pages, compiles translations, and encrypts leftover plaintext session strings.

### Existing SQLite data / نقل قاعدة SQLite

```bash
python -m src.tools.migrate_sqlite --sqlite data/bot.db
```

The script copies the current tables into `DATABASE_URL`, keeps ids, and encrypts session strings. Re-running it does not overwrite existing rows.

### Docker

```bash
cp .env.example .env
# fill BOT_TOKEN, API_ID, API_HASH, ADMIN_IDS, SESSION_ENCRYPTION_KEY
docker compose up --build
```

Compose starts Postgres 16 and overrides `DATABASE_URL` to the `db` service. The container runs `alembic upgrade head` before the bot.

## Development / التطوير

```bash
ruff check src tests alembic
ruff format --check src tests alembic
mypy src
pytest
```

Logs are JSON, one object per line. Shutdown stops the code monitor, the bot, the webhook server when it is running, and disposes the database engine.

## Commands / الأوامر

- `/start`, `/subscribe`, `/add_account`, `/my_accounts`, `/language`
- Admin: `/create_plan`, `/list_plans`, `/list_users`, `/view_user`, `/grant_subscription`
