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
- Account runtime: one Kurigram client per subscribed account, plugin commands, and plan gating
- Userbot commands: group admin, logging, auto-reply, AFK, PM protection, locks, mentions, broadcast, chat creation, star gifts, and an opt-in game notice

الخطط والاشتراك بنجوم تيليجرام وCrypto Pay، لوحة الإدارة، صفحات المعلومات، العربية والإنجليزية، إضافة الحساب مع ملفات الجهاز وإعادة محاولة تسجيل الدخول، مراقبة أكواد الدخول، التحقق بخطوتين، الحذف المنطقي، تصفح الملف والهدايا والمحادثات الخاصة، مديرو الفريق وروابط الدعوة ونقل الحساب، تدوير البروكسي، وإنشاء المجموعات المجدولة، ومحرك الحسابات مع أوامر الإضافات.

Kurigram clients are constructed only in `src/runtime/client_factory.py`.

## Account runtime / محرك الحسابات

`RUNTIME_ROLE=all` (the default) runs the control bot and the account runtime in one process. Docker Compose runs them apart: the `bot` service uses `RUNTIME_ROLE=bot` and the `worker` service uses `RUNTIME_ROLE=worker`.

The worker supervises the Kurigram clients for accounts on its shard (`account_id % WORKER_SHARD_COUNT == WORKER_SHARD_ID`). It reconnects with backoff, stops on a revoked session, and messages the owner. The bot wakes it with a `runtime_signals` row and Postgres `NOTIFY telegroup_runtime`. A poll covers missed notifications and SQLite.

Userbot commands use `USERBOT_PREFIX` (default `.`) and only outgoing messages from the account itself. `.help` / `.الاوامر` lists what the current plan allows. Arabic names are the primary commands, with English aliases. The full list is in [docs/commands.md](docs/commands.md). See [docs/plugins.md](docs/plugins.md) for how to add a plugin.

Each account menu has a Plugins screen. Admins choose which plugins a plan allows from the plan editor. New plans allow every plugin registered at creation time. A migration grants the built-in plugins to plans that already exist.

Accounts with an active subscription start when at least one allowed plugin is enabled. `ping`, `id`, and `help` are on by default. Group creation and the code monitor stay off until their existing buttons (or the Plugins screen) turn them on.

`python -m src.main` with `RUNTIME_ROLE=all` is the single-process setup. `python -m src.worker` is the worker. Apply schema changes with `alembic upgrade head` before starting either process.

Phase 2 tables: `plan_plugins`, `account_plugins`, `plugin_settings`, `runtime_signals`, `session_leases`.

Phase 3 tables: `auto_replies`, `pm_permits`, `chat_locks`. The migration does not grant the new plugins to plans that already exist. Open the plan editor and allow `admin`, `storage`, `autoreply`, `afk`, `pmpermit`, `locks`, `tagall`, `broadcast`, `create`, `gifts`, and `games`. New plans include every plugin registered when the plan is created. The Plugins screen can edit each plugin's settings (warning limit, log chat, mention cap, and so on).

Sharding is configured per process; there is no automatic assignment of accounts to workers yet. Downloads, sticker tools, converters, and the remaining userbot commands are phase 4.

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
