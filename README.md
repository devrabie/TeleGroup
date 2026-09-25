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
- Userbot commands: group admin, logging, auto-reply, AFK, PM protection, locks, mentions, broadcast, chat creation, star gifts, an opt-in game notice, downloads, stickers, translation, speech, optional OCR, ffmpeg conversion, and small account tools

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

Phase 3 tables: `auto_replies`, `pm_permits`, `chat_locks`. Revision 0003 does not grant those plugins to plans that already have an allowlist. Open the plan editor and allow `admin`, `storage`, `autoreply`, `afk`, `pmpermit`, `locks`, `tagall`, `broadcast`, `create`, `gifts`, and `games` if the plan was created before them. New plans include every plugin registered when the plan is created. The SQLite importer fills allowlists that are still empty; see below. The Plugins screen can edit each plugin's settings (warning limit, log chat, mention cap, and so on).

Revision `0004_phase4` grants `download`, `stickers`, `translate`, `tts`, `ocr`, `convert`, `telegraph`, `info`, `leave`, `repeat`, `profile`, `clock`, and `calc` to every plan that already exists. It only inserts missing rows. It does not turn plugins off and it does not delete allowlist rows. Run `alembic upgrade head` before starting the worker. Downgrade removes only those phase 4 names.

Sharding is configured per process; there is no automatic assignment of accounts to workers yet.

### Phase 4 media / الوسائط

Downloads (`yt-dlp`) and ffmpeg conversions run in a short-lived child process, not on the account's event loop. `MEDIA_WORKERS` is 1 or 2 (default 1). Each account defaults to one download at a time. `DOWNLOAD_MAX_MB` (default 50) and `DOWNLOAD_MAX_SECONDS` (default 600) are hard ceilings. Temporary files are deleted after upload. The account's existing proxy is passed to yt-dlp when the plugin setting `use_proxy` is on.

The account's proxy comes from the proxy already stored for that account (`build_proxy_dict`). There is no second proxy list.

If `ffmpeg`, `tesseract`, `yt-dlp`, `Pillow`, or `gTTS` is missing, the command tells you and the worker keeps running.

System packages:

| Where | Package | Needed for |
| --- | --- | --- |
| Docker image and the host | `ffmpeg` | gif, voice notes, mp3, YouTube audio extraction |
| Host only, optional | `tesseract-ocr`, `tesseract-ocr-eng`, `tesseract-ocr-ara` | `.استخراج` / `.ocr` |

The Docker image installs `ffmpeg` and does not install tesseract. Native install notes are in [deploy/README.md](deploy/README.md).

```bash
sudo apt-get install -y ffmpeg
# optional OCR
sudo apt-get install -y tesseract-ocr tesseract-ocr-eng tesseract-ocr-ara
```

Python packages `yt-dlp`, `Pillow`, and `gTTS` are installed with the project. Translation uses MyMemory (no key, 450 characters) unless `TRANSLATE_PROVIDER=libre` and `TRANSLATE_URL` point at a LibreTranslate server.

Still out of scope: playing other people's group games, cookies for private or age-restricted posts, animated sticker packs, and playlist downloads.

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

Apply the schema first, then copy into that empty database:

```bash
alembic upgrade head
python -m src.tools.migrate_sqlite --sqlite data/bot.db --verify
```

The script copies the current tables into `DATABASE_URL`, keeps ids, and encrypts session strings. Alembic 0002 grants the original plugins only to plans that exist when it runs. Plans copied afterwards would otherwise have an empty allowlist, and no account would start. The importer grants `ping`, `id`, `help`, `groups`, `codemon`, `admin`, `storage`, `autoreply`, `afk`, `pmpermit`, `locks`, `tagall`, `broadcast`, `create`, `gifts`, `games`, and the phase 4 plugins to every plan that still has no plugin rows. Plans that already have an allowlist are not changed. On a new database, `alembic upgrade head` runs while `plans` is still empty, so revision 0004 inserts nothing. The importer then fills those empty allowlists with the full default list. A production database that already has plans receives the phase 4 names from revision 0004 itself.

The default mode skips rows that already exist (`ON CONFLICT DO NOTHING`) and does not update them. A faithful copy needs an empty target database, which is what `alembic upgrade head` on a new PostgreSQL database gives you.

- `--upsert` updates existing rows, matched on the same unique key the skip uses (`telegram_id`, plan `name`, proxy string, sharing token, info-page key, or `id`).
- `--reset` deletes the copied tables and the plugin rows that hang off them, then inserts. Do not combine it with `--upsert`.
- `--verify` compares source and destination row counts for each copied table and checks that every plan has at least one plugin. It exits non-zero when they differ. Use it after a copy into an empty database or after `--reset`.

A row that fails to insert is logged with the table, an id or phone, and the exception type. The process then exits non-zero and prints a summary. Session strings are not written to the log.

### Native install (no Docker) / تثبيت بدون Docker

Example systemd units are in `deploy/`. They assume the checkout is `/opt/telegroup`, the virtualenv is `/opt/telegroup/venv`, a `telegroup` user can read `/opt/telegroup/.env`, and PostgreSQL is `postgresql.service`. Change those if the host differs. `.env` needs the same variables as `.env.example`.

`deploy/telegroup-bot.service` sets `RUNTIME_ROLE=bot` and runs `alembic upgrade head` in `ExecStartPre` before `python -m src.main`. `deploy/telegroup-worker.service` sets `RUNTIME_ROLE=worker` and starts `python -m src.worker` after the bot unit. `Environment=` in the unit overrides `RUNTIME_ROLE` from the env file, so a value left in `.env` does not switch the process. The worker does not run migrations; start the bot unit so `ExecStartPre` finishes before relying on the worker.

```bash
sudo cp deploy/telegroup-bot.service deploy/telegroup-worker.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now telegroup-bot telegroup-worker
```

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
pybabel compile -D base -d locales
```

Translation catalogs are `locales/*/LC_MESSAGES/base.po`. The gettext domain is `base`. `pybabel compile` needs `-D base`; without it, pybabel looks for `messages.po`. Startup still compiles catalogs through `compile_translations()`, and the Docker image does the same.

Logs are JSON, one object per line. Shutdown stops the code monitor, the bot, the webhook server when it is running, and disposes the database engine.

## Commands / الأوامر

- `/start`, `/subscribe`, `/add_account`, `/my_accounts`, `/language`
- Admin: `/create_plan`, `/list_plans`, `/list_users`, `/view_user`, `/grant_subscription`
