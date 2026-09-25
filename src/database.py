"""Persistence API for TeleGroup.

Public functions stay synchronous so handlers and the existing tests can call
them directly. Each one runs on a dedicated event loop and uses an async
SQLAlchemy session. PostgreSQL is the production database; tests and the
one-off SQLite importer can use ``sqlite+aiosqlite``.
"""

from __future__ import annotations

import logging
import random
import secrets
import sqlite3
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import aliased

from src.crypto import decrypt_session_value, encrypt_session, is_encrypted_session
from src.db.engine import (
    DB_FILE,
    current_database_url,
    get_engine,
    run_db,
    session_scope,
    sessionmaker,
    sqlite_file_path,
)
from src.db.models import (
    AccountManager,
    Base,
    DeviceProfile,
    GroupCreationLog,
    InfoPage,
    ManagedAccount,
    Plan,
    Proxy,
    SharingToken,
    Subscription,
    User,
)
from src.db.sqlutil import insert_for
from src.device_profiles import DEVICES

log = logging.getLogger(__name__)

SHARE_KIND_ADD = "add"
SHARE_KIND_TEAM = "team"
VALID_SHARE_KINDS = (SHARE_KIND_ADD, SHARE_KIND_TEAM)

SESSION_STATUS_OK = "ok"
SESSION_STATUS_INVALID = "invalid"
SESSION_STATUS_UNKNOWN = "unknown"

_INVALID_SESSION_MARKERS = (
    "authkeyunregistered",
    "sessionrevoked",
    "userdeactivated",
    "authkeyduplicated",
    "sessioninvalid",
    "session_invalid",
    "the key is not registered",
)

_BOOL_FIELDS = {
    "is_admin",
    "is_active",
    "is_working",
    "is_running",
    "code_monitor_enabled",
}

_DEFAULT_PAGES = {
    "en": {
        "privacy": "This is the default Privacy Policy. Please edit this text in the admin panel.",
        "disclaimer": "This is the default Disclaimer. Please edit this text in the admin panel.",
        "payment": (
            "This is the default Payment and Refund Policy. "
            "Please edit this text in the admin panel."
        ),
        "project": (
            "This is the default Project Information. Please edit this text in the admin panel."
        ),
        "features": (
            "This is the default Bot Features description. "
            "Please edit this text in the admin panel."
        ),
    },
    "ar": {
        "privacy": "هذه هي سياسة الخصوصية الافتراضية. يرجى تعديل هذا النص من لوحة التحكم.",
        "disclaimer": "هذا هو إخلاء المسؤولية الافتراضي. يرجى تعديل هذا النص من لوحة التحكم.",
        "payment": "هذه هي سياسة الدفع والاسترداد الافتراضية. يرجى تعديل هذا النص من لوحة التحكم.",
        "project": "هذه هي معلومات المشروع الافتراضية. يرجى تعديل هذا النص من لوحة التحكم.",
        "features": "هذا هو وصف ميزات البوت الافتراضي. يرجى تعديل هذا النص من لوحة التحكم.",
    },
}


def _utcnow() -> datetime:
    """Naive UTC, matching TIMESTAMP columns on both SQLite and PostgreSQL."""
    return datetime.now(UTC).replace(tzinfo=None)


def _normalize_value(key: str, value: Any, *, decrypt: bool) -> Any:
    if key == "session_string" and decrypt and isinstance(value, str):
        value = decrypt_session_value(value)
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            value = value.astimezone(UTC).replace(tzinfo=None)
        return value.replace(tzinfo=UTC).isoformat()
    if key in _BOOL_FIELDS and value is not None:
        return int(bool(value))
    return value


def _model_dict(obj: Any, *, decrypt: bool = False) -> dict[str, Any]:
    data: dict[str, Any] = {}
    for column in obj.__table__.columns:
        data[column.name] = _normalize_value(
            column.name, getattr(obj, column.name), decrypt=decrypt
        )
    return data


def _mapping_dict(row: Any, *, decrypt: bool = False) -> dict[str, Any]:
    return {
        str(key): _normalize_value(str(key), value, decrypt=decrypt) for key, value in row.items()
    }


def _rowcount(result: Any) -> int:
    return int(getattr(result, "rowcount", 0) or 0)


def _exclude_ids(exclude_id: int | set | list | tuple | None) -> list[int]:
    if exclude_id is None:
        return []
    if isinstance(exclude_id, (set, list, tuple)):
        return [int(item) for item in exclude_id if item is not None]
    return [int(exclude_id)]


async def _insert(session: Any, model: Any) -> Any:
    connection = await session.connection()
    return insert_for(connection, model)


def _run(coro: Any) -> Any:
    """Apply a test's patched ``DB_FILE`` before touching the engine."""
    from src.db import engine as engine_mod

    engine_mod.DB_FILE = DB_FILE
    return run_db(coro)


def get_db_connection() -> sqlite3.Connection:
    """SQLite connection for tests and diagnostics. PostgreSQL has no equivalent."""
    from src.db import engine as engine_mod

    engine_mod.DB_FILE = DB_FILE
    path = sqlite_file_path()
    if path is None:
        raise RuntimeError("get_db_connection() is only available for sqlite database URLs")
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=15.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA busy_timeout = 10000;")
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


async def _seed(session: Any) -> None:
    for device in DEVICES:
        platform = device["client_platform"]
        platform_str = platform.value if hasattr(platform, "value") else str(platform)
        exists = await session.scalar(
            select(DeviceProfile.id).where(
                DeviceProfile.device_model == device["device_model"],
                DeviceProfile.system_version == device["system_version"],
                DeviceProfile.app_version == device["app_version"],
                DeviceProfile.lang_code == device["lang_code"],
                DeviceProfile.client_platform == platform_str,
            )
        )
        if exists is None:
            session.add(
                DeviceProfile(
                    device_model=device["device_model"],
                    system_version=device["system_version"],
                    app_version=device["app_version"],
                    lang_code=device["lang_code"],
                    client_platform=platform_str,
                    api_id=device.get("api_id"),
                    api_hash=device.get("api_hash"),
                )
            )
    await session.flush()

    missing_ids = list(
        (
            await session.scalars(
                select(ManagedAccount.id).where(ManagedAccount.device_profile_id.is_(None))
            )
        ).all()
    )
    for account_id in missing_ids:
        profile_id = await session.scalar(select(DeviceProfile.id).order_by(func.random()).limit(1))
        if profile_id is None:
            break
        await session.execute(
            update(ManagedAccount)
            .where(ManagedAccount.id == account_id)
            .values(device_profile_id=profile_id)
        )

    for lang_code, pages in _DEFAULT_PAGES.items():
        for page_key, content in pages.items():
            stmt = (
                (await _insert(session, InfoPage))
                .values(page_key=page_key, lang_code=lang_code, content=content)
                .on_conflict_do_nothing(index_elements=["page_key", "lang_code"])
            )
            await session.execute(stmt)


async def _encrypt_plaintext_sessions(session: Any) -> int:
    rows = (await session.scalars(select(ManagedAccount))).all()
    changed = 0
    for account in rows:
        stored = account.session_string
        if not stored or is_encrypted_session(stored):
            continue
        encrypted = encrypt_session(stored)
        if encrypted and encrypted != stored:
            account.session_string = encrypted
            changed += 1
    if changed:
        log.info("Encrypted %s plaintext session strings.", changed)
    return changed


async def _initialize() -> None:
    await sessionmaker()
    engine = get_engine()
    if engine is None:
        raise RuntimeError("Database engine was not created")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with session_scope() as session:
        await _seed(session)
        await _encrypt_plaintext_sessions(session)
    log.info("Database initialized successfully.")


def initialize_database() -> None:
    log.info("Initializing database at: %s", current_database_url().split("@")[-1])
    _run(_initialize())


def encrypt_plaintext_session_rows() -> int:
    async def _go() -> int:
        async with session_scope() as session:
            return await _encrypt_plaintext_sessions(session)

    return _run(_go())


def looks_like_invalid_session(error: object | None) -> bool:
    if error is None:
        return False
    text = str(error).strip().lower()
    if not text:
        return False
    compact = "".join(ch for ch in text if ch.isalnum())
    for marker in _INVALID_SESSION_MARKERS:
        if marker.replace("_", "") in compact or marker in text:
            return True
    return False


def session_is_invalid(account: dict | None) -> bool:
    if not account:
        return False
    if account.get("session_status") == SESSION_STATUS_INVALID:
        return True
    if account.get("session_status") == SESSION_STATUS_OK:
        return False
    return looks_like_invalid_session(account.get("last_error"))


def ui_language_from_telegram(language_code: str | None) -> str:
    code = (language_code or "").lower().replace("-", "_")
    if code == "ar" or code.startswith("ar_"):
        return "ar"
    return "en"


def _new_sharing_token() -> str:
    return secrets.token_urlsafe(12)


async def _random_proxy_id(
    session: Any, exclude_id: int | set | list | tuple | None = None
) -> int | None:
    excluded = _exclude_ids(exclude_id)
    stmt = select(Proxy.id).where(Proxy.is_working.is_(True))
    if excluded:
        stmt = stmt.where(Proxy.id.not_in(excluded))
    stmt = stmt.order_by(func.random()).limit(1)
    found = await session.scalar(stmt)
    if found is not None:
        return int(found)
    fallback = select(Proxy.id)
    if excluded:
        fallback = fallback.where(Proxy.id.not_in(excluded))
    fallback = fallback.order_by(Proxy.last_checked.asc(), func.random()).limit(1)
    found = await session.scalar(fallback)
    return int(found) if found is not None else None


async def _internal_user_id(session: Any, telegram_id: int) -> int | None:
    found = await session.scalar(select(User.id).where(User.telegram_id == telegram_id))
    return int(found) if found is not None else None


async def _user_is_owner(session: Any, account_id: int, telegram_user_id: int) -> bool:
    stmt = (
        select(ManagedAccount.id)
        .where(
            ManagedAccount.id == account_id,
            ManagedAccount.deleted_at.is_(None),
            ManagedAccount.user_id
            == select(User.id).where(User.telegram_id == telegram_user_id).scalar_subquery(),
        )
        .limit(1)
    )
    return await session.scalar(stmt) is not None


async def _user_owns(session: Any, account_id: int, telegram_user_id: int) -> bool:
    manager_exists = (
        select(AccountManager.id)
        .where(
            AccountManager.owner_user_id == ManagedAccount.user_id,
            AccountManager.manager_telegram_id == telegram_user_id,
        )
        .exists()
    )
    stmt = (
        select(ManagedAccount.id)
        .join(User, ManagedAccount.user_id == User.id)
        .where(
            ManagedAccount.id == account_id,
            ManagedAccount.deleted_at.is_(None),
            (User.telegram_id == telegram_user_id) | manager_exists,
        )
        .limit(1)
    )
    return await session.scalar(stmt) is not None


async def _get_or_create_user(session: Any, telegram_id: int) -> dict[str, Any] | None:
    stmt = (
        (await _insert(session, User))
        .values(telegram_id=telegram_id, first_name="User", language_code="en")
        .on_conflict_do_nothing(index_elements=["telegram_id"])
    )
    await session.execute(stmt)
    user = await session.scalar(select(User).where(User.telegram_id == telegram_id))
    return _model_dict(user) if user else None


async def _get_user_details(session: Any, telegram_id: int) -> dict[str, Any] | None:
    user = await session.scalar(select(User).where(User.telegram_id == telegram_id))
    if user is None:
        return None
    sub_row = (
        await session.execute(
            select(Subscription, Plan.name.label("plan_name"))
            .join(Plan, Subscription.plan_id == Plan.id)
            .where(Subscription.user_id == user.id, Subscription.is_active.is_(True))
            .order_by(Subscription.end_date.desc())
            .limit(1)
        )
    ).first()
    subscription = None
    if sub_row is not None:
        subscription = _model_dict(sub_row[0])
        subscription["plan_name"] = sub_row[1]
    accounts = (
        await session.scalars(
            select(ManagedAccount).where(
                ManagedAccount.user_id == user.id, ManagedAccount.deleted_at.is_(None)
            )
        )
    ).all()
    account_rows = []
    for account in accounts:
        full = _model_dict(account)
        account_rows.append(
            {
                "id": full["id"],
                "phone": full["phone"],
                "is_active": full["is_active"],
                "code_monitor_enabled": full["code_monitor_enabled"],
                "last_error": full["last_error"],
                "session_status": full["session_status"],
                "next_creation_time": full["next_creation_time"],
            }
        )
    return {"user": _model_dict(user), "subscription": subscription, "accounts": account_rows}


async def _get_plan(session: Any, plan_id: int) -> dict[str, Any] | None:
    plan = await session.get(Plan, plan_id)
    return _model_dict(plan) if plan else None


async def _get_user_by_username(session: Any, username: str) -> dict[str, Any] | None:
    clean = username.lstrip("@").strip()
    if not clean:
        return None
    user = await session.scalar(select(User).where(func.lower(User.username) == clean.lower()))
    if user is None:
        return None
    data = _model_dict(user)
    return {
        "telegram_id": data["telegram_id"],
        "first_name": data["first_name"],
        "username": data["username"],
    }


async def _mark_session_status(
    session: Any, account_id: int, status: str, error_message: str | None = None
) -> bool:
    if account_id is None or status not in {
        SESSION_STATUS_OK,
        SESSION_STATUS_INVALID,
        SESSION_STATUS_UNKNOWN,
    }:
        return False
    account = await session.scalar(
        select(ManagedAccount).where(
            ManagedAccount.id == account_id, ManagedAccount.deleted_at.is_(None)
        )
    )
    if account is None:
        return False
    account.session_status = status
    if status == SESSION_STATUS_INVALID:
        account.last_error = error_message or "SESSION_INVALID"
    elif status == SESSION_STATUS_OK and account.last_error in {None, "SESSION_INVALID"}:
        account.last_error = None
    return True


def add_plan(
    name: str,
    price_stars: int,
    price_usd: float,
    duration_days: int,
    max_accounts: int,
    daily_group_limit: int,
) -> bool:
    async def _go() -> bool:
        async with session_scope() as session:
            try:
                session.add(
                    Plan(
                        name=name,
                        price_stars=price_stars,
                        price_usd=price_usd,
                        duration_days=duration_days,
                        max_accounts=max_accounts,
                        daily_group_limit=daily_group_limit,
                    )
                )
                await session.flush()
                return True
            except IntegrityError:
                await session.rollback()
                log.warning("Plan with name '%s' already exists.", name)
                return False

    try:
        return _run(_go())
    except Exception:
        log.exception("Failed to add plan '%s'", name)
        return False


def get_plan_by_id(plan_id: int) -> dict[str, Any] | None:
    async def _go() -> dict[str, Any] | None:
        async with session_scope() as session:
            return await _get_plan(session, plan_id)

    try:
        return _run(_go())
    except Exception:
        log.exception("Failed to retrieve plan %s", plan_id)
        return None


def get_all_plans(active_only: bool = True) -> list[dict[str, Any]]:
    async def _go() -> list[dict[str, Any]]:
        async with session_scope() as session:
            stmt = select(Plan)
            if active_only:
                stmt = stmt.where(Plan.is_active.is_(True))
            plans = (await session.scalars(stmt)).all()
            return [_model_dict(plan) for plan in plans]

    try:
        return _run(_go())
    except Exception:
        log.exception("Failed to retrieve plans")
        return []


def update_plan(plan_id: int, **kwargs: Any) -> tuple[bool, str]:
    if not kwargs:
        log.warning("update_plan called with no fields to update.")
        return False, "plan_update_error_no_fields"
    allowed = {
        "name",
        "price_stars",
        "price_usd",
        "duration_days",
        "max_accounts",
        "daily_group_limit",
        "is_active",
    }
    values: dict[str, Any] = {}
    for key, value in kwargs.items():
        if key not in allowed:
            log.warning("Attempted to update an invalid plan field: %s", key)
            return False, f"plan_update_error_invalid_field:{key}"
        if key == "is_active":
            values[key] = bool(value)
        else:
            values[key] = value
    if not values:
        return False, "plan_update_error_no_valid_fields"

    async def _go() -> tuple[bool, str]:
        async with session_scope() as session:
            try:
                await session.execute(update(Plan).where(Plan.id == plan_id).values(**values))
                return True, "plan_update_success"
            except IntegrityError:
                await session.rollback()
                return False, "plan_update_error_duplicate_name"

    try:
        return _run(_go())
    except Exception:
        log.exception("Failed to update plan %s", plan_id)
        return False, "db_error"


def get_info_page_content(page_key: str, lang_code: str) -> str | None:
    async def _go() -> str | None:
        async with session_scope() as session:
            content = await session.scalar(
                select(InfoPage.content).where(
                    InfoPage.page_key == page_key, InfoPage.lang_code == lang_code
                )
            )
            if content is None and lang_code != "en":
                log.warning(
                    "No content for page '%s' in lang '%s', falling back to 'en'.",
                    page_key,
                    lang_code,
                )
                content = await session.scalar(
                    select(InfoPage.content).where(
                        InfoPage.page_key == page_key, InfoPage.lang_code == "en"
                    )
                )
            return content

    try:
        return _run(_go())
    except Exception:
        log.exception("Failed to retrieve info page '%s' for lang '%s'", page_key, lang_code)
        return None


def update_info_page_content(page_key: str, lang_code: str, content: str) -> bool:
    async def _go() -> bool:
        async with session_scope() as session:
            result = await session.execute(
                update(InfoPage)
                .where(InfoPage.page_key == page_key, InfoPage.lang_code == lang_code)
                .values(content=content)
            )
            return _rowcount(result) > 0

    try:
        return _run(_go())
    except Exception:
        log.exception("Failed to update info page '%s' for lang '%s'", page_key, lang_code)
        return False


def get_all_users() -> list[dict[str, Any]]:
    async def _go() -> list[dict[str, Any]]:
        async with session_scope() as session:
            users = (await session.scalars(select(User).order_by(User.created_at.desc()))).all()
            return [_model_dict(user) for user in users]

    try:
        return _run(_go())
    except Exception:
        log.exception("Failed to retrieve users")
        return []


def grant_subscription(telegram_id: int, plan_id: int, duration_days: int) -> tuple[bool, str]:
    async def _go() -> tuple[bool, str]:
        async with session_scope() as session:
            user_id = await _internal_user_id(session, telegram_id)
            if user_id is None:
                log.warning("No user found with telegram_id %s to grant subscription.", telegram_id)
                return False, "user_not_found"
            await session.execute(
                update(Subscription)
                .where(Subscription.user_id == user_id, Subscription.is_active.is_(True))
                .values(is_active=False)
            )
            now = _utcnow()
            session.add(
                Subscription(
                    user_id=user_id,
                    plan_id=plan_id,
                    start_date=now,
                    end_date=now + timedelta(days=duration_days),
                    is_active=True,
                )
            )
            await session.flush()
            return True, "grant_subscription_success"

    try:
        return _run(_go())
    except Exception:
        log.exception("Failed to grant subscription to user %s", telegram_id)
        return False, "db_error"


def batch_insert_proxies(proxies: list[str]) -> int:
    clean = [proxy.strip() for proxy in proxies if proxy and proxy.strip()]
    if not clean:
        return 0

    async def _go() -> int:
        async with session_scope() as session:
            existing = set(
                (
                    await session.scalars(
                        select(Proxy.proxy_string).where(Proxy.proxy_string.in_(clean))
                    )
                ).all()
            )
            inserted = 0
            for proxy in clean:
                if proxy in existing:
                    continue
                session.add(Proxy(proxy_string=proxy, is_working=True, last_checked=_utcnow()))
                inserted += 1
            await session.execute(
                update(Proxy)
                .where(Proxy.proxy_string.in_(clean))
                .values(is_working=True, last_checked=_utcnow())
            )
            log.info("Batch inserted proxies. %s new proxies were added.", inserted)
            return inserted

    try:
        return _run(_go())
    except Exception:
        log.exception("Failed to batch insert proxies")
        return 0


def count_working_proxies() -> int:
    async def _go() -> int:
        async with session_scope() as session:
            count = await session.scalar(
                select(func.count()).select_from(Proxy).where(Proxy.is_working.is_(True))
            )
            return int(count or 0)

    try:
        return _run(_go())
    except Exception:
        log.exception("Failed to count working proxies")
        return 0


def warn_if_no_working_proxies() -> None:
    async def _go() -> None:
        async with session_scope() as session:
            total = int(await session.scalar(select(func.count()).select_from(Proxy)) or 0)
            working = int(
                await session.scalar(
                    select(func.count()).select_from(Proxy).where(Proxy.is_working.is_(True))
                )
                or 0
            )
        if total > 0 and working == 0:
            log.error(
                "All %s stored proxies are marked not working. "
                "Refresh the proxy list or mark them working again.",
                total,
            )

    try:
        _run(_go())
    except Exception:
        log.exception("Failed to check proxy health")


def get_random_proxy_id(exclude_id: int | set | list | tuple | None = None) -> int | None:
    async def _go() -> int | None:
        async with session_scope() as session:
            return await _random_proxy_id(session, exclude_id)

    try:
        return _run(_go())
    except Exception:
        log.exception("Failed to retrieve a random proxy")
        return None


def get_random_device_profile() -> dict[str, Any] | None:
    async def _go() -> dict[str, Any] | None:
        async with session_scope() as session:
            profile = await session.scalar(select(DeviceProfile).order_by(func.random()).limit(1))
            return _model_dict(profile) if profile else None

    try:
        return _run(_go())
    except Exception:
        log.exception("Failed to retrieve a random device profile")
        return None


def get_device_profile_by_account_id(account_id: int) -> dict[str, Any] | None:
    async def _go() -> dict[str, Any] | None:
        async with session_scope() as session:
            profile = await session.scalar(
                select(DeviceProfile)
                .join(ManagedAccount, ManagedAccount.device_profile_id == DeviceProfile.id)
                .where(ManagedAccount.id == account_id)
            )
            return _model_dict(profile) if profile else None

    try:
        return _run(_go())
    except Exception:
        log.exception("Failed to get device profile for account %s", account_id)
        return None


def add_managed_account(
    user_id: int, phone: str, session_string: str, device_profile_id: int
) -> bool:
    async def _go() -> bool:
        async with session_scope() as session:
            proxy_id = await _random_proxy_id(session)
            if proxy_id is None:
                log.warning("No available proxies to assign to new account %s.", phone)
            internal_user_id = await _internal_user_id(session, user_id)
            if internal_user_id is None:
                log.error(
                    "Cannot add account. User with Telegram ID %s not found in users table.",
                    user_id,
                )
                return False
            encrypted = encrypt_session(session_string) or ""
            existing = await session.scalar(
                select(ManagedAccount).where(
                    ManagedAccount.user_id == internal_user_id,
                    ManagedAccount.phone == phone,
                    ManagedAccount.deleted_at.is_not(None),
                )
            )
            if existing is not None:
                existing.session_string = encrypted
                existing.proxy_id = proxy_id
                existing.device_profile_id = device_profile_id
                existing.is_active = False
                existing.code_monitor_enabled = False
                existing.deleted_at = None
                existing.last_error = None
                existing.session_status = SESSION_STATUS_OK
                existing.backoff_level = 0
                existing.next_creation_time = None
                return True
            session.add(
                ManagedAccount(
                    user_id=internal_user_id,
                    phone=phone,
                    session_string=encrypted,
                    proxy_id=proxy_id,
                    device_profile_id=device_profile_id,
                    is_active=False,
                    is_running=False,
                    code_monitor_enabled=False,
                    session_status=SESSION_STATUS_OK,
                )
            )
            await session.flush()
            return True

    try:
        return _run(_go())
    except IntegrityError:
        log.warning("Attempted to add a duplicate, active account with phone number %s.", phone)
        return False
    except Exception:
        log.exception("Failed to add or reactivate managed account %s for user %s", phone, user_id)
        return False


def delete_managed_account(account_id: int, telegram_user_id: int) -> bool:
    async def _go() -> bool:
        async with session_scope() as session:
            if not await _user_owns(session, account_id, telegram_user_id):
                return False
            result = await session.execute(
                update(ManagedAccount)
                .where(ManagedAccount.id == account_id, ManagedAccount.deleted_at.is_(None))
                .values(deleted_at=_utcnow(), is_active=False)
            )
            return _rowcount(result) > 0

    try:
        return _run(_go())
    except Exception:
        log.exception("Failed to soft-delete account %s", account_id)
        return False


def toggle_code_monitor(account_id: int, telegram_user_id: int) -> bool | None:
    async def _go() -> bool | None:
        async with session_scope() as session:
            if not await _user_owns(session, account_id, telegram_user_id):
                log.warning(
                    "User %s tried to toggle code monitor on non-existent or unowned account %s.",
                    telegram_user_id,
                    account_id,
                )
                return None
            account = await session.scalar(
                select(ManagedAccount).where(
                    ManagedAccount.id == account_id, ManagedAccount.deleted_at.is_(None)
                )
            )
            if account is None:
                return None
            account.code_monitor_enabled = not bool(account.code_monitor_enabled)
            return bool(account.code_monitor_enabled)

    try:
        return _run(_go())
    except Exception:
        log.exception("Failed to toggle code monitor for account %s", account_id)
        return None


def toggle_account_status(account_id: int, telegram_user_id: int) -> bool | None:
    async def _go() -> bool | None:
        async with session_scope() as session:
            if not await _user_owns(session, account_id, telegram_user_id):
                log.warning(
                    "User %s tried to toggle non-existent or unowned account %s.",
                    telegram_user_id,
                    account_id,
                )
                return None
            account = await session.scalar(
                select(ManagedAccount).where(
                    ManagedAccount.id == account_id, ManagedAccount.deleted_at.is_(None)
                )
            )
            if account is None:
                return None
            new_status = not bool(account.is_active)
            account.is_active = new_status
            if new_status:
                account.next_creation_time = None
                account.backoff_level = 0
                account.last_error = None
            return new_status

    try:
        return _run(_go())
    except Exception:
        log.exception("Failed to toggle status for account %s", account_id)
        return None


def reassign_proxy(account_id: int, telegram_user_id: int) -> tuple[bool, str]:
    async def _go() -> tuple[bool, str]:
        async with session_scope() as session:
            if not await _user_owns(session, account_id, telegram_user_id):
                return False, "db_error"
            account = await session.scalar(
                select(ManagedAccount).where(
                    ManagedAccount.id == account_id, ManagedAccount.deleted_at.is_(None)
                )
            )
            if account is None:
                return False, "db_error"
            new_proxy_id = await _random_proxy_id(session, exclude_id=account.proxy_id)
            if new_proxy_id is None:
                new_proxy_id = await _random_proxy_id(session)
            if new_proxy_id is None:
                return False, "no_available_proxies"
            account.proxy_id = new_proxy_id
            return True, "proxy_update_success"

    try:
        return _run(_go())
    except Exception:
        log.exception("Failed to reassign proxy for account %s", account_id)
        return False, "db_error"


def get_account_session_string(account_id: int) -> str | None:
    async def _go() -> str | None:
        async with session_scope() as session:
            stored = await session.scalar(
                select(ManagedAccount.session_string).where(ManagedAccount.id == account_id)
            )
            return decrypt_session_value(stored)

    try:
        return _run(_go())
    except Exception:
        log.exception("Failed to get session string for account %s", account_id)
        return None


def get_account_stats(account_id: int) -> int:
    async def _go() -> int:
        async with session_scope() as session:
            count = await session.scalar(
                select(func.count())
                .select_from(GroupCreationLog)
                .where(GroupCreationLog.account_id == account_id)
            )
            return int(count or 0)

    try:
        return _run(_go())
    except Exception:
        log.exception("Failed to get stats for account %s", account_id)
        return 0


def get_groups_for_account(account_id: int) -> list[dict[str, Any]]:
    async def _go() -> list[dict[str, Any]]:
        async with session_scope() as session:
            rows = (
                await session.scalars(
                    select(GroupCreationLog)
                    .where(GroupCreationLog.account_id == account_id)
                    .order_by(GroupCreationLog.creation_timestamp.desc())
                )
            ).all()
            result = []
            for row in rows:
                full = _model_dict(row)
                result.append(
                    {
                        "id": full["id"],
                        "group_id": full["group_id"],
                        "group_name": full["group_name"],
                        "creation_timestamp": full["creation_timestamp"],
                    }
                )
            return result

    try:
        return _run(_go())
    except Exception:
        log.exception("Failed to get groups for account %s", account_id)
        return []


def get_group_log_details(group_log_id: int) -> dict[str, Any] | None:
    async def _go() -> dict[str, Any] | None:
        async with session_scope() as session:
            row = await session.get(GroupCreationLog, group_log_id)
            if row is None:
                return None
            full = _model_dict(row)
            return {
                "group_name": full["group_name"],
                "creation_timestamp": full["creation_timestamp"],
            }

    try:
        return _run(_go())
    except Exception:
        log.exception("Failed to get group log details for log %s", group_log_id)
        return None


def mark_proxy_as_bad(proxy_id: int | None) -> None:
    if proxy_id is None:
        return

    async def _go() -> None:
        async with session_scope() as session:
            await session.execute(
                update(Proxy)
                .where(Proxy.id == proxy_id)
                .values(is_working=False, last_checked=_utcnow())
            )

    try:
        _run(_go())
        log.warning("Marked proxy %s as bad.", proxy_id)
    except Exception:
        log.exception("Failed to mark proxy %s as bad", proxy_id)


def assign_account_proxy(account_id: int | None, proxy_id: int | None) -> bool:
    if account_id is None:
        return False

    async def _go() -> bool:
        async with session_scope() as session:
            result = await session.execute(
                update(ManagedAccount)
                .where(ManagedAccount.id == account_id, ManagedAccount.deleted_at.is_(None))
                .values(proxy_id=proxy_id)
            )
            return _rowcount(result) > 0

    try:
        return _run(_go())
    except Exception:
        log.exception("Failed to assign proxy %s to account %s", proxy_id, account_id)
        return False


def rotate_account_proxy(account_id: int, failed_proxy_id: int | None = None) -> int | None:
    async def _go() -> int | None:
        async with session_scope() as session:
            if failed_proxy_id is not None:
                await session.execute(
                    update(Proxy)
                    .where(Proxy.id == failed_proxy_id)
                    .values(is_working=False, last_checked=_utcnow())
                )
            new_proxy_id = await _random_proxy_id(session, exclude_id=failed_proxy_id)
            if new_proxy_id is None:
                new_proxy_id = await _random_proxy_id(session)
            if new_proxy_id is not None:
                await session.execute(
                    update(ManagedAccount)
                    .where(ManagedAccount.id == account_id, ManagedAccount.deleted_at.is_(None))
                    .values(proxy_id=new_proxy_id)
                )
            return new_proxy_id

    try:
        return _run(_go())
    except Exception:
        log.exception("Failed to rotate proxy for account %s", account_id)
        return None


def set_account_flood_wait(account_id: int, wait_until_timestamp: datetime) -> bool:
    async def _go() -> bool:
        async with session_scope() as session:
            result = await session.execute(
                update(ManagedAccount)
                .where(ManagedAccount.id == account_id)
                .values(flood_wait_until=wait_until_timestamp)
            )
            return _rowcount(result) > 0

    try:
        ok = _run(_go())
        if ok:
            log.info("Account %s is flood-waited until %s.", account_id, wait_until_timestamp)
        return ok
    except Exception:
        log.exception("Failed to set flood wait for account %s", account_id)
        return False


def get_proxy_string(proxy_id: int | None) -> str | None:
    if proxy_id is None:
        return None

    async def _go() -> str | None:
        async with session_scope() as session:
            return await session.scalar(select(Proxy.proxy_string).where(Proxy.id == proxy_id))

    try:
        return _run(_go())
    except Exception:
        log.exception("Failed to get proxy string for id %s", proxy_id)
        return None


def _active_subscription_filters(now: datetime) -> list[Any]:
    return [
        Subscription.is_active.is_(True),
        Subscription.end_date >= now,
    ]


def get_eligible_accounts() -> list[dict[str, Any]]:
    async def _go() -> list[dict[str, Any]]:
        now = _utcnow()
        async with session_scope() as session:
            stmt = (
                select(
                    ManagedAccount.id.label("account_id"),
                    ManagedAccount.session_string,
                    ManagedAccount.proxy_id,
                    Plan.daily_group_limit,
                    User.telegram_id,
                    DeviceProfile.device_model,
                    DeviceProfile.system_version,
                    DeviceProfile.app_version,
                    DeviceProfile.lang_code,
                    DeviceProfile.api_id,
                    DeviceProfile.api_hash,
                )
                .join(User, ManagedAccount.user_id == User.id)
                .join(Subscription, User.id == Subscription.user_id)
                .join(Plan, Subscription.plan_id == Plan.id)
                .outerjoin(DeviceProfile, ManagedAccount.device_profile_id == DeviceProfile.id)
                .where(
                    ManagedAccount.is_active.is_(True),
                    ManagedAccount.deleted_at.is_(None),
                    *_active_subscription_filters(now),
                    (ManagedAccount.flood_wait_until.is_(None))
                    | (ManagedAccount.flood_wait_until < now),
                    (ManagedAccount.next_creation_time.is_(None))
                    | (ManagedAccount.next_creation_time < now),
                    (ManagedAccount.session_status.is_(None))
                    | (ManagedAccount.session_status != SESSION_STATUS_INVALID),
                )
            )
            rows = (await session.execute(stmt)).mappings().all()
            return [_mapping_dict(row, decrypt=True) for row in rows]

    try:
        return _run(_go())
    except Exception:
        log.exception("Failed to retrieve eligible accounts")
        return []


def get_code_monitor_accounts() -> list[dict[str, Any]]:
    async def _go() -> list[dict[str, Any]]:
        now = _utcnow()
        async with session_scope() as session:
            stmt = (
                select(
                    ManagedAccount.id.label("account_id"),
                    ManagedAccount.phone,
                    ManagedAccount.session_string,
                    ManagedAccount.proxy_id,
                    User.telegram_id,
                    DeviceProfile.device_model,
                    DeviceProfile.system_version,
                    DeviceProfile.app_version,
                    DeviceProfile.lang_code,
                    DeviceProfile.api_id,
                    DeviceProfile.api_hash,
                )
                .join(User, ManagedAccount.user_id == User.id)
                .join(Subscription, User.id == Subscription.user_id)
                .join(Plan, Subscription.plan_id == Plan.id)
                .outerjoin(DeviceProfile, ManagedAccount.device_profile_id == DeviceProfile.id)
                .where(
                    ManagedAccount.code_monitor_enabled.is_(True),
                    ManagedAccount.deleted_at.is_(None),
                    *_active_subscription_filters(now),
                    (ManagedAccount.session_status.is_(None))
                    | (ManagedAccount.session_status != SESSION_STATUS_INVALID),
                )
            )
            rows = (await session.execute(stmt)).mappings().all()
            return [_mapping_dict(row, decrypt=True) for row in rows]

    try:
        return _run(_go())
    except Exception:
        log.exception("Failed to retrieve code-monitor accounts")
        return []


def get_account_runtime_details(account_id: int) -> dict[str, Any] | None:
    async def _go() -> dict[str, Any] | None:
        async with session_scope() as session:
            stmt = (
                select(
                    ManagedAccount.id.label("account_id"),
                    ManagedAccount.phone,
                    ManagedAccount.session_string,
                    ManagedAccount.proxy_id,
                    ManagedAccount.code_monitor_enabled,
                    ManagedAccount.is_active,
                    User.telegram_id,
                    DeviceProfile.device_model,
                    DeviceProfile.system_version,
                    DeviceProfile.app_version,
                    DeviceProfile.lang_code,
                    DeviceProfile.api_id,
                    DeviceProfile.api_hash,
                )
                .join(User, ManagedAccount.user_id == User.id)
                .outerjoin(DeviceProfile, ManagedAccount.device_profile_id == DeviceProfile.id)
                .where(ManagedAccount.id == account_id, ManagedAccount.deleted_at.is_(None))
            )
            row = (await session.execute(stmt)).mappings().first()
            return _mapping_dict(row, decrypt=True) if row else None

    try:
        return _run(_go())
    except Exception:
        log.exception("Failed to get runtime details for account %s", account_id)
        return None


def get_internal_user_id(telegram_id: int) -> int | None:
    async def _go() -> int | None:
        async with session_scope() as session:
            return await _internal_user_id(session, telegram_id)

    try:
        return _run(_go())
    except Exception:
        log.exception("Failed to resolve internal user id for %s", telegram_id)
        return None


def user_is_account_owner(account_id: int, telegram_user_id: int) -> bool:
    async def _go() -> bool:
        async with session_scope() as session:
            return await _user_is_owner(session, account_id, telegram_user_id)

    try:
        return _run(_go())
    except Exception:
        log.exception("Failed to check owner of account %s", account_id)
        return False


def user_owns_account(account_id: int, telegram_user_id: int) -> bool:
    async def _go() -> bool:
        async with session_scope() as session:
            return await _user_owns(session, account_id, telegram_user_id)

    try:
        return _run(_go())
    except Exception:
        log.exception("Failed to check access to account %s", account_id)
        return False


def get_or_create_sharing_token(owner_telegram_id: int, kind: str) -> str | None:
    if kind not in VALID_SHARE_KINDS:
        return None

    async def _go() -> str | None:
        async with session_scope() as session:
            owner_user_id = await _internal_user_id(session, owner_telegram_id)
            if owner_user_id is None:
                return None
            existing = await session.scalar(
                select(SharingToken.token).where(
                    SharingToken.owner_user_id == owner_user_id, SharingToken.kind == kind
                )
            )
            if existing:
                return existing
            for _ in range(5):
                token = _new_sharing_token()
                try:
                    async with session.begin_nested():
                        session.add(
                            SharingToken(owner_user_id=owner_user_id, kind=kind, token=token)
                        )
                        await session.flush()
                    return token
                except IntegrityError:
                    continue
            return None

    try:
        return _run(_go())
    except Exception:
        log.exception("Failed to get/create %s token for %s", kind, owner_telegram_id)
        return None


def rotate_sharing_token(owner_telegram_id: int, kind: str) -> str | None:
    if kind not in VALID_SHARE_KINDS:
        return None

    async def _go() -> str | None:
        async with session_scope() as session:
            owner_user_id = await _internal_user_id(session, owner_telegram_id)
            if owner_user_id is None:
                return None
            for _ in range(5):
                token = _new_sharing_token()
                try:
                    async with session.begin_nested():
                        row = await session.scalar(
                            select(SharingToken).where(
                                SharingToken.owner_user_id == owner_user_id,
                                SharingToken.kind == kind,
                            )
                        )
                        if row is None:
                            session.add(
                                SharingToken(owner_user_id=owner_user_id, kind=kind, token=token)
                            )
                        else:
                            row.token = token
                            row.created_at = _utcnow()
                        await session.flush()
                    return token
                except IntegrityError:
                    continue
            return None

    try:
        return _run(_go())
    except Exception:
        log.exception("Failed to rotate %s token for %s", kind, owner_telegram_id)
        return None


def resolve_sharing_token(token: str) -> dict[str, Any] | None:
    if not token:
        return None

    async def _go() -> dict[str, Any] | None:
        async with session_scope() as session:
            row = (
                (
                    await session.execute(
                        select(
                            SharingToken.kind,
                            User.id.label("owner_user_id"),
                            User.telegram_id.label("owner_telegram_id"),
                            User.first_name.label("owner_name"),
                            User.username.label("owner_username"),
                        )
                        .join(User, User.id == SharingToken.owner_user_id)
                        .where(SharingToken.token == token)
                    )
                )
                .mappings()
                .first()
            )
            return _mapping_dict(row) if row else None

    try:
        return _run(_go())
    except Exception:
        log.exception("Failed to resolve sharing token")
        return None


def add_account_manager(owner_telegram_id: int, manager_telegram_id: int) -> str:
    if owner_telegram_id == manager_telegram_id:
        return "self"

    async def _go() -> str:
        async with session_scope() as session:
            owner_user_id = await _internal_user_id(session, owner_telegram_id)
            if owner_user_id is None:
                return "not_found"
            try:
                session.add(
                    AccountManager(
                        owner_user_id=owner_user_id, manager_telegram_id=manager_telegram_id
                    )
                )
                await session.flush()
                return "ok"
            except IntegrityError:
                await session.rollback()
                return "exists"

    try:
        return _run(_go())
    except Exception:
        log.exception("Failed to add manager %s for %s", manager_telegram_id, owner_telegram_id)
        return "error"


def remove_account_manager(owner_telegram_id: int, manager_telegram_id: int) -> bool:
    async def _go() -> bool:
        async with session_scope() as session:
            owner_user_id = await _internal_user_id(session, owner_telegram_id)
            if owner_user_id is None:
                return False
            row = await session.scalar(
                select(AccountManager).where(
                    AccountManager.owner_user_id == owner_user_id,
                    AccountManager.manager_telegram_id == manager_telegram_id,
                )
            )
            if row is None:
                return False
            await session.delete(row)
            return True

    try:
        return _run(_go())
    except Exception:
        log.exception("Failed to remove manager %s for %s", manager_telegram_id, owner_telegram_id)
        return False


def list_account_managers(owner_telegram_id: int) -> list[dict[str, Any]]:
    async def _go() -> list[dict[str, Any]]:
        async with session_scope() as session:
            manager_user = aliased(User)
            owner_id = (
                select(User.id).where(User.telegram_id == owner_telegram_id).scalar_subquery()
            )
            rows = (
                (
                    await session.execute(
                        select(
                            AccountManager.manager_telegram_id,
                            manager_user.first_name,
                            manager_user.username,
                            AccountManager.created_at,
                        )
                        .outerjoin(
                            manager_user,
                            manager_user.telegram_id == AccountManager.manager_telegram_id,
                        )
                        .where(AccountManager.owner_user_id == owner_id)
                        .order_by(AccountManager.created_at)
                    )
                )
                .mappings()
                .all()
            )
            return [_mapping_dict(row) for row in rows]

    try:
        return _run(_go())
    except Exception:
        log.exception("Failed to list managers for %s", owner_telegram_id)
        return []


def list_owners_for_manager(manager_telegram_id: int) -> list[dict[str, Any]]:
    async def _go() -> list[dict[str, Any]]:
        async with session_scope() as session:
            rows = (
                (
                    await session.execute(
                        select(User.telegram_id, User.first_name, User.username)
                        .join(AccountManager, User.id == AccountManager.owner_user_id)
                        .where(AccountManager.manager_telegram_id == manager_telegram_id)
                        .order_by(User.first_name)
                    )
                )
                .mappings()
                .all()
            )
            return [_mapping_dict(row) for row in rows]

    try:
        return _run(_go())
    except Exception:
        log.exception("Failed to list owners for manager %s", manager_telegram_id)
        return []


def get_user_by_username(username: str) -> dict[str, Any] | None:
    if not username:
        return None

    async def _go() -> dict[str, Any] | None:
        async with session_scope() as session:
            return await _get_user_by_username(session, username)

    try:
        return _run(_go())
    except Exception:
        log.exception("Failed to look up username")
        return None


def get_accessible_accounts(actor_telegram_id: int) -> dict[str, Any]:
    async def _go() -> dict[str, Any]:
        result: dict[str, Any] = {"own": [], "shared": []}
        async with session_scope() as session:
            own = await _get_user_details(session, actor_telegram_id)
            if own:
                result["own"] = own["accounts"]
            owners = (
                await session.execute(
                    select(User.telegram_id, User.first_name, User.username, User.id)
                    .join(AccountManager, User.id == AccountManager.owner_user_id)
                    .where(AccountManager.manager_telegram_id == actor_telegram_id)
                    .order_by(User.first_name)
                )
            ).all()
            for owner in owners:
                accounts = (
                    await session.scalars(
                        select(ManagedAccount).where(
                            ManagedAccount.user_id == owner.id,
                            ManagedAccount.deleted_at.is_(None),
                        )
                    )
                ).all()
                packed = []
                for account in accounts:
                    full = _model_dict(account)
                    packed.append(
                        {
                            "id": full["id"],
                            "phone": full["phone"],
                            "is_active": full["is_active"],
                            "code_monitor_enabled": full["code_monitor_enabled"],
                            "last_error": full["last_error"],
                            "session_status": full["session_status"],
                            "next_creation_time": full["next_creation_time"],
                        }
                    )
                result["shared"].append(
                    {
                        "owner_telegram_id": owner.telegram_id,
                        "owner_name": owner.first_name,
                        "owner_username": owner.username,
                        "accounts": packed,
                    }
                )
        return result

    try:
        return _run(_go())
    except Exception:
        log.exception("Failed to list accessible accounts for %s", actor_telegram_id)
        return {"own": [], "shared": []}


def get_groups_created_today(account_id: int) -> int:
    async def _go() -> int:
        since = _utcnow() - timedelta(hours=24)
        async with session_scope() as session:
            count = await session.scalar(
                select(func.count())
                .select_from(GroupCreationLog)
                .where(
                    GroupCreationLog.account_id == account_id,
                    GroupCreationLog.creation_timestamp >= since,
                )
            )
            return int(count or 0)

    try:
        return _run(_go())
    except Exception:
        log.exception("Failed to count groups for account %s", account_id)
        return 0


def log_group_creation(account_id: int, group_id: int, group_name: str) -> bool:
    async def _go() -> bool:
        async with session_scope() as session:
            session.add(
                GroupCreationLog(account_id=account_id, group_id=group_id, group_name=group_name)
            )
            await session.flush()
            return True

    try:
        return _run(_go())
    except Exception:
        log.exception("Failed to log group creation for account %s", account_id)
        return False


def update_account_schedule(account_id: int, daily_group_limit: int) -> bool:
    now = _utcnow()
    if daily_group_limit <= 0:
        next_time = now + timedelta(days=999)
    else:
        interval_minutes = max(1, (24 * 60) / daily_group_limit)
        variation_seconds = random.uniform(120, 300)
        if random.choice([True, False]):
            variation_seconds *= -1
        next_time = now + timedelta(minutes=interval_minutes, seconds=variation_seconds)

    async def _go() -> bool:
        async with session_scope() as session:
            result = await session.execute(
                update(ManagedAccount)
                .where(ManagedAccount.id == account_id)
                .values(next_creation_time=next_time, backoff_level=0, last_error=None)
            )
            return _rowcount(result) > 0

    try:
        ok = _run(_go())
        if ok:
            log.info(
                "Scheduled next group creation for account %s at %s",
                account_id,
                next_time.strftime("%Y-%m-%d %H:%M:%S"),
            )
        return ok
    except Exception:
        log.exception("Failed to update schedule for account %s", account_id)
        return False


def apply_error_backoff(
    account_id: int, error_message: str, wait_seconds: int | None = None
) -> bool:
    async def _go() -> bool:
        async with session_scope() as session:
            account = await session.get(ManagedAccount, account_id)
            if account is None:
                log.error("Cannot apply backoff for non-existent account_id: %s", account_id)
                return False
            current_level = account.backoff_level or 0
            new_level = current_level + 1
            now = _utcnow()
            if wait_seconds is not None:
                buffer_minutes = random.uniform(1, 5)
                next_time = now + timedelta(seconds=wait_seconds, minutes=buffer_minutes)
            else:
                delay_minutes = 30 * (2**current_level)
                random_minutes = random.uniform(0, 10)
                total_delay_minutes = min(delay_minutes + random_minutes, 3 * 24 * 60)
                next_time = now + timedelta(minutes=total_delay_minutes)
            account.next_creation_time = next_time
            account.backoff_level = new_level
            account.last_error = str(error_message)
            if looks_like_invalid_session(error_message):
                await _mark_session_status(
                    session, account_id, SESSION_STATUS_INVALID, error_message
                )
            return True

    try:
        return _run(_go())
    except Exception:
        log.exception("Failed to apply backoff for account %s", account_id)
        return False


def mark_session_status(account_id: int, status: str, error_message: str | None = None) -> bool:
    async def _go() -> bool:
        async with session_scope() as session:
            return await _mark_session_status(session, account_id, status, error_message)

    try:
        return _run(_go())
    except Exception:
        log.exception("Failed to set session_status=%s for account %s", status, account_id)
        return False


def mark_session_invalid(account_id: int, error_message: str | None = None) -> bool:
    log.warning("Marking account %s session as invalid: %s", account_id, error_message)
    return mark_session_status(account_id, SESSION_STATUS_INVALID, error_message)


def mark_session_ok(account_id: int) -> bool:
    return mark_session_status(account_id, SESSION_STATUS_OK)


def update_user_details(user: Any) -> bool:
    ui_lang = ui_language_from_telegram(getattr(user, "language_code", None))
    first_name = getattr(user, "first_name", None) or "User"

    async def _go() -> bool:
        async with session_scope() as session:
            stmt = (
                (await _insert(session, User))
                .values(
                    telegram_id=user.id,
                    first_name=first_name,
                    username=user.username,
                    language_code=ui_lang,
                )
                .on_conflict_do_nothing(index_elements=["telegram_id"])
            )
            await session.execute(stmt)
            await session.execute(
                update(User)
                .where(User.telegram_id == user.id)
                .values(first_name=first_name, username=user.username)
            )
            return True

    try:
        return _run(_go())
    except Exception:
        log.exception("Database error in update_user_details for %s", getattr(user, "id", None))
        return False


def get_or_create_user(telegram_id: int) -> dict[str, Any] | None:
    async def _go() -> dict[str, Any] | None:
        async with session_scope() as session:
            return await _get_or_create_user(session, telegram_id)

    try:
        return _run(_go())
    except Exception:
        log.exception("Database error in get_or_create_user for %s", telegram_id)
        return None


def set_user_language(telegram_id: int, lang_code: str) -> bool:
    async def _go() -> bool:
        async with session_scope() as session:
            await _get_or_create_user(session, telegram_id)
            await session.execute(
                update(User).where(User.telegram_id == telegram_id).values(language_code=lang_code)
            )
            return True

    try:
        return _run(_go())
    except Exception:
        log.exception("Failed to set language for user %s", telegram_id)
        return False


def get_user_language(telegram_id: int) -> str:
    user = get_or_create_user(telegram_id)
    if user and user.get("language_code"):
        return str(user["language_code"])
    return "en"


def get_user_details(telegram_id: int) -> dict[str, Any] | None:
    async def _go() -> dict[str, Any] | None:
        async with session_scope() as session:
            return await _get_user_details(session, telegram_id)

    try:
        return _run(_go())
    except Exception:
        log.exception("Failed to get details for user %s", telegram_id)
        return None


def get_account_details(account_id: int) -> dict[str, Any] | None:
    async def _go() -> dict[str, Any] | None:
        async with session_scope() as session:
            row = (
                await session.execute(
                    select(ManagedAccount, Proxy.proxy_string)
                    .outerjoin(Proxy, Proxy.id == ManagedAccount.proxy_id)
                    .where(ManagedAccount.id == account_id)
                )
            ).first()
            if row is None:
                return None
            account, proxy_string = row
            last_creation = await session.scalar(
                select(func.max(GroupCreationLog.creation_timestamp)).where(
                    GroupCreationLog.account_id == account.id
                )
            )
            total = await session.scalar(
                select(func.count())
                .select_from(GroupCreationLog)
                .where(GroupCreationLog.account_id == account.id)
            )
            full = _model_dict(account)
            return {
                "id": full["id"],
                "phone": full["phone"],
                "is_active": full["is_active"],
                "code_monitor_enabled": full["code_monitor_enabled"],
                "proxy_id": full["proxy_id"],
                "proxy_string": proxy_string,
                "next_creation_time": full["next_creation_time"],
                "backoff_level": full["backoff_level"],
                "last_error": full["last_error"],
                "session_status": full["session_status"],
                "last_creation_time": _normalize_value(
                    "last_creation_time", last_creation, decrypt=False
                ),
                "total_groups": int(total or 0),
            }

    try:
        return _run(_go())
    except Exception:
        log.exception("Failed to get details for account %s", account_id)
        return None


def transfer_managed_account(
    account_id: int, sender_telegram_id: int, recipient_identifier: str
) -> tuple[bool, str, dict[str, Any] | None]:
    async def _go() -> tuple[bool, str, dict[str, Any] | None]:
        async with session_scope() as session:
            if not await _user_is_owner(session, account_id, sender_telegram_id):
                return False, "not_owner", None
            sender_user_id = await _internal_user_id(session, sender_telegram_id)
            clean_target = recipient_identifier.strip()
            recipient: dict[str, Any] | None = None
            if clean_target.isdigit():
                target_tid = int(clean_target)
                if target_tid == sender_telegram_id:
                    return False, "self_transfer", None
                recipient = await _get_or_create_user(session, target_tid)
            elif clean_target.startswith("@") or clean_target.isalnum():
                found = await _get_user_by_username(session, clean_target)
                if found:
                    user = await session.scalar(
                        select(User).where(User.telegram_id == found["telegram_id"])
                    )
                    recipient = _model_dict(user) if user else None
            if not recipient:
                return False, "recipient_not_found", None
            if recipient["telegram_id"] == sender_telegram_id:
                return False, "self_transfer", None
            details = await _get_user_details(session, recipient["telegram_id"])
            if not details or not details.get("subscription"):
                return False, "recipient_no_subscription", recipient
            plan = await _get_plan(session, details["subscription"]["plan_id"])
            if not plan:
                return False, "recipient_no_subscription", recipient
            if len(details.get("accounts") or []) >= plan["max_accounts"]:
                return False, "recipient_plan_full", recipient
            result = await session.execute(
                update(ManagedAccount)
                .where(
                    ManagedAccount.id == account_id,
                    ManagedAccount.user_id == sender_user_id,
                    ManagedAccount.deleted_at.is_(None),
                )
                .values(user_id=details["user"]["id"])
            )
            if _rowcount(result) > 0:
                return True, "ok", recipient
            return False, "account_not_found", recipient

    try:
        return _run(_go())
    except Exception:
        log.exception("Failed to transfer account %s", account_id)
        return False, "db_error", None


def get_system_stats() -> dict[str, int] | None:
    async def _go() -> dict[str, int]:
        now = _utcnow()
        since = now - timedelta(hours=24)
        async with session_scope() as session:
            return {
                "total_users": int(
                    await session.scalar(select(func.count()).select_from(User)) or 0
                ),
                "active_subscriptions": int(
                    await session.scalar(
                        select(func.count())
                        .select_from(Subscription)
                        .where(Subscription.is_active.is_(True), Subscription.end_date >= now)
                    )
                    or 0
                ),
                "total_managed_accounts": int(
                    await session.scalar(select(func.count()).select_from(ManagedAccount)) or 0
                ),
                "groups_created_total": int(
                    await session.scalar(select(func.count()).select_from(GroupCreationLog)) or 0
                ),
                "groups_created_today": int(
                    await session.scalar(
                        select(func.count())
                        .select_from(GroupCreationLog)
                        .where(GroupCreationLog.creation_timestamp >= since)
                    )
                    or 0
                ),
            }

    try:
        return _run(_go())
    except Exception:
        log.exception("Failed to retrieve system stats")
        return None


__all__ = [
    "DB_FILE",
    "initialize_database",
    "encrypt_plaintext_session_rows",
]
