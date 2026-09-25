"""Admin plan grants, single-use activation codes, and the grant audit log.

Paid checkouts still use ``grant_subscription``, which replaces the current
subscription and does not write an audit row. This module is the admin path.
"""

from __future__ import annotations

import logging
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import IntegrityError

from src.database import _run, _utcnow
from src.db.engine import session_scope
from src.db.models import ActivationCode, Plan, PlanGrant, Subscription, User

log = logging.getLogger(__name__)

CODE_PREFIX = "act_"
SOURCE_MANUAL = "manual"
SOURCE_CODE = "code"
ACTION_GRANT = "grant"
ACTION_EXTEND = "extend"
ACTION_REPLACE = "replace"
ACTION_REVOKE = "revoke"

ERR_INVALID_TARGET = "invalid_target"
ERR_USERNAME_UNKNOWN = "username_unknown"
ERR_PLAN_NOT_FOUND = "plan_not_found"
ERR_INVALID_DURATION = "invalid_duration"
ERR_CHOOSE_MODE = "choose_mode"
ERR_NO_SUBSCRIPTION = "no_subscription"
ERR_DB = "db_error"
ERR_INVALID_BATCH = "invalid_batch"
ERR_INVALID_EXPIRY = "invalid_expiry"
ERR_NOT_REVOCABLE = "not_revocable"
ERR_INVALID_CODE = "invalid"
ERR_USED = "used"
ERR_EXPIRED = "expired"
ERR_REVOKED = "revoked"

MAX_DURATION_DAYS = 3650
MIN_BATCH = 1
MAX_BATCH = 50
SEARCH_PAGE_SIZE = 8
CODE_PAGE_SIZE = 6
_CODE_RANDOM_BYTES = 18
_MAX_TELEGRAM_ID = 2**63 - 1

GrantMode = Literal["grant", "extend", "replace"]
CodeStatus = Literal["unused", "used", "expired", "revoked"]


@dataclass(frozen=True)
class SubscriptionView:
    plan_id: int
    plan_name: str
    end_date: str
    is_current: bool


@dataclass(frozen=True)
class TargetLookup:
    status: str
    telegram_id: int | None = None
    first_name: str | None = None
    username: str | None = None
    subscription: SubscriptionView | None = None


@dataclass(frozen=True)
class UserMatch:
    telegram_id: int
    first_name: str
    username: str | None


@dataclass(frozen=True)
class UserSearchPage:
    items: tuple[UserMatch, ...]
    total: int
    page: int
    page_size: int


@dataclass(frozen=True)
class GrantOutcome:
    ok: bool
    error: str | None = None
    action: str | None = None
    plan_name: str | None = None
    plan_id: int | None = None
    duration_days: int | None = None
    end_date: str | None = None
    telegram_id: int | None = None
    user_created: bool = False


@dataclass(frozen=True)
class IssuedCode:
    id: int
    code: str
    plan_id: int
    plan_name: str
    duration_days: int
    expires_at: str | None


@dataclass(frozen=True)
class IssueOutcome:
    ok: bool
    error: str | None = None
    codes: tuple[IssuedCode, ...] = ()


@dataclass(frozen=True)
class RedeemOutcome:
    ok: bool
    error: str | None = None
    action: str | None = None
    plan_name: str | None = None
    duration_days: int | None = None
    end_date: str | None = None


@dataclass(frozen=True)
class CodeListItem:
    id: int
    code: str
    plan_name: str
    duration_days: int
    status: str
    expires_at: str | None


@dataclass(frozen=True)
class CodeListPage:
    items: tuple[CodeListItem, ...]
    total: int
    page: int
    page_size: int
    status: str


@dataclass(frozen=True)
class CodeDetail:
    id: int
    code: str
    plan_name: str
    duration_days: int
    status: str
    created_by_telegram_id: int
    created_at: str | None
    expires_at: str | None
    used_at: str | None
    redeemed_telegram_id: int | None
    redeemed_first_name: str | None
    redeemed_username: str | None
    revoked_at: str | None


def is_activation_payload(payload: str) -> bool:
    return payload.startswith(CODE_PREFIX)


def activation_link(bot_username: str, code: str) -> str:
    return f"https://t.me/{bot_username}?start={code}"


def parse_duration_days(text: str) -> int | None:
    raw = text.strip()
    if not raw.isdigit():
        return None
    value = int(raw)
    if value < 1 or value > MAX_DURATION_DAYS:
        return None
    return value


def parse_batch_count(text: str) -> int | None:
    raw = text.strip()
    if not raw.isdigit():
        return None
    value = int(raw)
    if value < MIN_BATCH or value > MAX_BATCH:
        return None
    return value


def parse_link_expiry(text: str, now: datetime | None = None) -> datetime | None:
    """A future expiry from a day count or a ``YYYY-MM-DD`` date. ``None`` is invalid."""
    moment = _naive(now or _utcnow())
    raw = text.strip()
    if raw.isdigit():
        days = int(raw)
        if days < 1 or days > MAX_DURATION_DAYS:
            return None
        return moment + timedelta(days=days)
    for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M"):
        try:
            parsed = datetime.strptime(raw, fmt)
        except ValueError:
            continue
        if fmt == "%Y-%m-%d":
            parsed = parsed.replace(hour=23, minute=59, second=59)
        if parsed <= moment:
            return None
        return parsed
    return None


def resolve_grant_target(raw: str) -> TargetLookup:
    text = raw.strip()
    if not text or len(text) > 64:
        return TargetLookup(status=ERR_INVALID_TARGET)
    if text.startswith("@"):
        return _lookup_username(text[1:])
    if text.isdigit():
        return _lookup_telegram_id(int(text))
    if " " not in text:
        return _lookup_username(text)
    return TargetLookup(status=ERR_INVALID_TARGET)


def search_users(query: str, *, page: int = 0, page_size: int = SEARCH_PAGE_SIZE) -> UserSearchPage:
    term = query.strip().lstrip("@")
    safe_page = max(page, 0)
    size = page_size if page_size > 0 else SEARCH_PAGE_SIZE
    empty = UserSearchPage(items=(), total=0, page=safe_page, page_size=size)
    if not term or len(term) > 64:
        return empty

    async def _go() -> UserSearchPage:
        async with session_scope() as session:
            pattern = _like_pattern(term)
            name_match = func.lower(User.first_name).like(pattern, escape="\\")
            username_match = func.lower(func.coalesce(User.username, "")).like(pattern, escape="\\")
            condition = name_match | username_match
            if term.isdigit():
                condition = condition | (User.telegram_id == int(term))
            total = int(
                await session.scalar(select(func.count()).select_from(User).where(condition)) or 0
            )
            rows = (
                await session.scalars(
                    select(User)
                    .where(condition)
                    .order_by(User.first_name, User.id)
                    .limit(size)
                    .offset(safe_page * size)
                )
            ).all()
            items = tuple(
                UserMatch(
                    telegram_id=int(row.telegram_id),
                    first_name=row.first_name,
                    username=row.username,
                )
                for row in rows
            )
            return UserSearchPage(items=items, total=total, page=safe_page, page_size=size)

    try:
        return _run(_go())
    except Exception:
        log.exception("Failed to search users")
        return empty


def load_grant_target(telegram_id: int) -> TargetLookup:
    if not _valid_telegram_id(telegram_id):
        return TargetLookup(status=ERR_INVALID_TARGET)

    async def _go() -> TargetLookup:
        async with session_scope() as session:
            user = await session.scalar(select(User).where(User.telegram_id == telegram_id))
            if user is None:
                return TargetLookup(status="absent", telegram_id=telegram_id)
            return await _target_from_user(session, user, status="ready")

    try:
        return _run(_go())
    except Exception:
        log.exception("Failed to load grant target %s", telegram_id)
        return TargetLookup(status=ERR_DB, telegram_id=telegram_id)


def apply_plan_grant(
    *,
    telegram_id: int,
    plan_id: int,
    duration_days: int,
    mode: GrantMode,
    admin_telegram_id: int,
    source: str = SOURCE_MANUAL,
    create_user: bool = False,
    activation_code_id: int | None = None,
) -> GrantOutcome:
    if not _valid_telegram_id(telegram_id) or not _valid_telegram_id(admin_telegram_id):
        return GrantOutcome(ok=False, error=ERR_INVALID_TARGET, telegram_id=telegram_id)
    if duration_days < 1 or duration_days > MAX_DURATION_DAYS:
        return GrantOutcome(ok=False, error=ERR_INVALID_DURATION, telegram_id=telegram_id)
    if mode not in {ACTION_GRANT, ACTION_EXTEND, ACTION_REPLACE}:
        return GrantOutcome(ok=False, error=ERR_DB, telegram_id=telegram_id)
    if source not in {SOURCE_MANUAL, SOURCE_CODE}:
        return GrantOutcome(ok=False, error=ERR_DB, telegram_id=telegram_id)

    async def _go() -> GrantOutcome:
        async with session_scope() as session:
            plan = await session.get(Plan, plan_id)
            if plan is None:
                return GrantOutcome(ok=False, error=ERR_PLAN_NOT_FOUND, telegram_id=telegram_id)
            user = await session.scalar(select(User).where(User.telegram_id == telegram_id))
            now = _utcnow()
            current = await _current_subscription(session, int(user.id), now) if user else None
            if mode == ACTION_GRANT and current is not None:
                return GrantOutcome(
                    ok=False,
                    error=ERR_CHOOSE_MODE,
                    telegram_id=telegram_id,
                    plan_id=plan_id,
                    plan_name=plan.name,
                    duration_days=duration_days,
                )
            created = False
            if user is None:
                if not create_user:
                    return GrantOutcome(ok=False, error=ERR_INVALID_TARGET, telegram_id=telegram_id)
                user = User(telegram_id=telegram_id, first_name="User", language_code="en")
                session.add(user)
                await session.flush()
                created = True
            action, end_date = await _apply_subscription(
                session,
                user_id=int(user.id),
                plan_id=int(plan.id),
                duration_days=duration_days,
                mode=mode,
                now=now,
                current=current,
            )
            _add_audit(
                session,
                admin_telegram_id=admin_telegram_id,
                target_user_id=int(user.id),
                plan_id=int(plan.id),
                duration_days=duration_days,
                source=source,
                action=action,
                activation_code_id=activation_code_id,
            )
            await session.flush()
            return GrantOutcome(
                ok=True,
                action=action,
                plan_name=plan.name,
                plan_id=int(plan.id),
                duration_days=duration_days,
                end_date=_iso(end_date),
                telegram_id=telegram_id,
                user_created=created,
            )

    try:
        return _run(_go())
    except Exception:
        log.exception("Failed to grant plan %s to %s", plan_id, telegram_id)
        return GrantOutcome(ok=False, error=ERR_DB, telegram_id=telegram_id)


def revoke_subscription(*, telegram_id: int, admin_telegram_id: int) -> GrantOutcome:
    if not _valid_telegram_id(telegram_id) or not _valid_telegram_id(admin_telegram_id):
        return GrantOutcome(ok=False, error=ERR_INVALID_TARGET, telegram_id=telegram_id)

    async def _go() -> GrantOutcome:
        async with session_scope() as session:
            user = await session.scalar(select(User).where(User.telegram_id == telegram_id))
            if user is None:
                return GrantOutcome(ok=False, error=ERR_NO_SUBSCRIPTION, telegram_id=telegram_id)
            now = _utcnow()
            rows = (
                await session.scalars(
                    select(Subscription)
                    .where(Subscription.user_id == user.id, Subscription.is_active.is_(True))
                    .order_by(Subscription.end_date.desc())
                )
            ).all()
            if not rows:
                return GrantOutcome(ok=False, error=ERR_NO_SUBSCRIPTION, telegram_id=telegram_id)
            primary = rows[0]
            plan = await session.get(Plan, primary.plan_id)
            for row in rows:
                row.is_active = False
                if _naive(row.end_date) > now:
                    row.end_date = now
            _add_audit(
                session,
                admin_telegram_id=admin_telegram_id,
                target_user_id=int(user.id),
                plan_id=int(primary.plan_id),
                duration_days=0,
                source=SOURCE_MANUAL,
                action=ACTION_REVOKE,
                activation_code_id=None,
            )
            await session.flush()
            return GrantOutcome(
                ok=True,
                action=ACTION_REVOKE,
                plan_name=plan.name if plan else None,
                plan_id=int(primary.plan_id),
                duration_days=0,
                end_date=_iso(now),
                telegram_id=telegram_id,
            )

    try:
        return _run(_go())
    except Exception:
        log.exception("Failed to revoke subscription for %s", telegram_id)
        return GrantOutcome(ok=False, error=ERR_DB, telegram_id=telegram_id)


def issue_activation_codes(
    *,
    plan_id: int,
    duration_days: int,
    count: int,
    admin_telegram_id: int,
    expires_at: datetime | None = None,
) -> IssueOutcome:
    if not _valid_telegram_id(admin_telegram_id):
        return IssueOutcome(ok=False, error=ERR_INVALID_TARGET)
    if duration_days < 1 or duration_days > MAX_DURATION_DAYS:
        return IssueOutcome(ok=False, error=ERR_INVALID_DURATION)
    if count < MIN_BATCH or count > MAX_BATCH:
        return IssueOutcome(ok=False, error=ERR_INVALID_BATCH)
    now = _utcnow()
    expiry = _naive(expires_at) if expires_at is not None else None
    if expiry is not None and expiry <= now:
        return IssueOutcome(ok=False, error=ERR_INVALID_EXPIRY)

    async def _go() -> IssueOutcome:
        async with session_scope() as session:
            plan = await session.get(Plan, plan_id)
            if plan is None:
                return IssueOutcome(ok=False, error=ERR_PLAN_NOT_FOUND)
            issued: list[ActivationCode] = []
            for _ in range(count):
                row = await _insert_code(
                    session,
                    plan_id=int(plan.id),
                    duration_days=duration_days,
                    admin_telegram_id=admin_telegram_id,
                    expires_at=expiry,
                )
                issued.append(row)
            await session.flush()
            codes = tuple(
                IssuedCode(
                    id=int(row.id),
                    code=row.code,
                    plan_id=int(plan.id),
                    plan_name=plan.name,
                    duration_days=duration_days,
                    expires_at=_iso(expiry) if expiry is not None else None,
                )
                for row in issued
            )
            return IssueOutcome(ok=True, codes=codes)

    try:
        return _run(_go())
    except Exception:
        log.exception("Failed to issue activation codes for plan %s", plan_id)
        return IssueOutcome(ok=False, error=ERR_DB)


def list_activation_codes(
    status: str, *, page: int = 0, page_size: int = CODE_PAGE_SIZE
) -> CodeListPage:
    safe_page = max(page, 0)
    size = page_size if page_size > 0 else CODE_PAGE_SIZE
    if status not in {"unused", "used", "expired", "revoked"}:
        return CodeListPage(items=(), total=0, page=safe_page, page_size=size, status=status)

    async def _go() -> CodeListPage:
        async with session_scope() as session:
            now = _utcnow()
            condition = _status_filter(status, now)
            total = int(
                await session.scalar(
                    select(func.count()).select_from(ActivationCode).where(condition)
                )
                or 0
            )
            rows = (
                await session.execute(
                    select(ActivationCode, Plan.name)
                    .join(Plan, Plan.id == ActivationCode.plan_id)
                    .where(condition)
                    .order_by(ActivationCode.id.desc())
                    .limit(size)
                    .offset(safe_page * size)
                )
            ).all()
            items = tuple(
                CodeListItem(
                    id=int(code.id),
                    code=code.code,
                    plan_name=plan_name,
                    duration_days=int(code.duration_days),
                    status=_code_status(code, now),
                    expires_at=_iso(code.expires_at) if code.expires_at else None,
                )
                for code, plan_name in rows
            )
            return CodeListPage(
                items=items, total=total, page=safe_page, page_size=size, status=status
            )

    try:
        return _run(_go())
    except Exception:
        log.exception("Failed to list activation codes")
        return CodeListPage(items=(), total=0, page=safe_page, page_size=size, status=status)


def get_activation_code(code_id: int) -> CodeDetail | None:
    async def _go() -> CodeDetail | None:
        async with session_scope() as session:
            row = (
                await session.execute(
                    select(ActivationCode, Plan.name, User)
                    .join(Plan, Plan.id == ActivationCode.plan_id)
                    .outerjoin(User, User.id == ActivationCode.used_by_user_id)
                    .where(ActivationCode.id == code_id)
                )
            ).first()
            if row is None:
                return None
            code, plan_name, redeemer = row
            now = _utcnow()
            return CodeDetail(
                id=int(code.id),
                code=code.code,
                plan_name=plan_name,
                duration_days=int(code.duration_days),
                status=_code_status(code, now),
                created_by_telegram_id=int(code.created_by_telegram_id),
                created_at=_iso(code.created_at) if code.created_at else None,
                expires_at=_iso(code.expires_at) if code.expires_at else None,
                used_at=_iso(code.used_at) if code.used_at else None,
                redeemed_telegram_id=int(redeemer.telegram_id) if redeemer else None,
                redeemed_first_name=redeemer.first_name if redeemer else None,
                redeemed_username=redeemer.username if redeemer else None,
                revoked_at=_iso(code.revoked_at) if code.revoked_at else None,
            )

    try:
        return _run(_go())
    except Exception:
        log.exception("Failed to load activation code %s", code_id)
        return None


def revoke_activation_code(*, code_id: int, admin_telegram_id: int) -> str:
    """Return ``ok`` or an error code. Unused codes only."""
    if not _valid_telegram_id(admin_telegram_id):
        return ERR_INVALID_TARGET

    async def _go() -> str:
        async with session_scope() as session:
            now = _utcnow()
            claimed = (
                await session.execute(
                    update(ActivationCode)
                    .where(
                        ActivationCode.id == code_id,
                        ActivationCode.used_at.is_(None),
                        ActivationCode.revoked_at.is_(None),
                    )
                    .values(revoked_at=now, revoked_by_telegram_id=admin_telegram_id)
                    .returning(ActivationCode.id)
                )
            ).scalar_one_or_none()
            if claimed is None:
                return ERR_NOT_REVOCABLE
            return "ok"

    try:
        return _run(_go())
    except Exception:
        log.exception("Failed to revoke activation code %s", code_id)
        return ERR_DB


def redeem_activation_code(code: str, telegram_id: int) -> RedeemOutcome:
    """Claim a code with one conditional update, then extend or activate.

    The ``UPDATE ... WHERE used_at IS NULL`` is the race gate. Two redeemers
    cannot both match it. A failed claim is rolled back with the subscription
    write because both happen in the same transaction.
    """
    token = code.strip()
    if not token or not _valid_telegram_id(telegram_id):
        return RedeemOutcome(ok=False, error=ERR_INVALID_CODE)

    async def _go() -> RedeemOutcome:
        async with session_scope() as session:
            now = _utcnow()
            row = await session.scalar(select(ActivationCode).where(ActivationCode.code == token))
            if row is None:
                return RedeemOutcome(ok=False, error=ERR_INVALID_CODE)
            early = _terminal_error(row, now)
            if early is not None:
                return RedeemOutcome(ok=False, error=early)
            plan = await session.get(Plan, row.plan_id)
            if plan is None:
                return RedeemOutcome(ok=False, error=ERR_PLAN_NOT_FOUND)
            user = await _ensure_user(session, telegram_id)
            claimed = (
                await session.execute(
                    update(ActivationCode)
                    .where(
                        ActivationCode.id == row.id,
                        ActivationCode.used_at.is_(None),
                        ActivationCode.revoked_at.is_(None),
                        or_(
                            ActivationCode.expires_at.is_(None),
                            ActivationCode.expires_at > now,
                        ),
                    )
                    .values(used_at=now, used_by_user_id=int(user.id))
                    .returning(ActivationCode.id)
                )
            ).scalar_one_or_none()
            if claimed is None:
                fresh = await session.get(ActivationCode, row.id)
                error = _terminal_error(fresh, now) if fresh is not None else ERR_INVALID_CODE
                return RedeemOutcome(ok=False, error=error or ERR_USED)
            current = await _current_subscription(session, int(user.id), now)
            action, end_date = await _apply_subscription(
                session,
                user_id=int(user.id),
                plan_id=int(plan.id),
                duration_days=int(row.duration_days),
                mode=ACTION_EXTEND,
                now=now,
                current=current,
            )
            _add_audit(
                session,
                admin_telegram_id=int(row.created_by_telegram_id),
                target_user_id=int(user.id),
                plan_id=int(plan.id),
                duration_days=int(row.duration_days),
                source=SOURCE_CODE,
                action=action,
                activation_code_id=int(row.id),
            )
            await session.flush()
            return RedeemOutcome(
                ok=True,
                action=action,
                plan_name=plan.name,
                duration_days=int(row.duration_days),
                end_date=_iso(end_date),
            )

    try:
        return _run(_go())
    except Exception:
        log.exception("Failed to redeem an activation code")
        return RedeemOutcome(ok=False, error=ERR_DB)


def list_plan_grants(*, telegram_id: int | None = None) -> list[dict[str, Any]]:
    async def _go() -> list[dict[str, Any]]:
        async with session_scope() as session:
            stmt = (
                select(
                    PlanGrant.id,
                    PlanGrant.admin_telegram_id,
                    User.telegram_id.label("target_telegram_id"),
                    PlanGrant.plan_id,
                    PlanGrant.duration_days,
                    PlanGrant.source,
                    PlanGrant.action,
                    PlanGrant.activation_code_id,
                    PlanGrant.created_at,
                )
                .join(User, User.id == PlanGrant.target_user_id)
                .order_by(PlanGrant.id.asc())
            )
            if telegram_id is not None:
                stmt = stmt.where(User.telegram_id == telegram_id)
            rows = (await session.execute(stmt)).mappings().all()
            result: list[dict[str, Any]] = []
            for row in rows:
                item = dict(row)
                created = item.get("created_at")
                if isinstance(created, datetime):
                    item["created_at"] = _iso(created)
                result.append(item)
            return result

    try:
        return _run(_go())
    except Exception:
        log.exception("Failed to list plan grants")
        return []


def _lookup_telegram_id(telegram_id: int) -> TargetLookup:
    if not _valid_telegram_id(telegram_id):
        return TargetLookup(status=ERR_INVALID_TARGET)
    return load_grant_target(telegram_id)


def _lookup_username(username: str) -> TargetLookup:
    clean = username.strip().lstrip("@")
    if not clean:
        return TargetLookup(status=ERR_INVALID_TARGET)

    async def _go() -> TargetLookup:
        async with session_scope() as session:
            user = await session.scalar(
                select(User).where(func.lower(User.username) == clean.lower())
            )
            if user is None:
                return TargetLookup(status=ERR_USERNAME_UNKNOWN, username=clean)
            return await _target_from_user(session, user, status="ready")

    try:
        return _run(_go())
    except Exception:
        log.exception("Failed to look up @%s", clean)
        return TargetLookup(status=ERR_DB, username=clean)


async def _target_from_user(session: Any, user: User, *, status: str) -> TargetLookup:
    now = _utcnow()
    current = await _current_subscription(session, int(user.id), now)
    shown = current
    if shown is None:
        shown = await session.scalar(
            select(Subscription)
            .where(Subscription.user_id == user.id, Subscription.is_active.is_(True))
            .order_by(Subscription.end_date.desc())
            .limit(1)
        )
    subscription = None
    if shown is not None:
        plan = await session.get(Plan, shown.plan_id)
        subscription = SubscriptionView(
            plan_id=int(shown.plan_id),
            plan_name=plan.name if plan else str(shown.plan_id),
            end_date=_iso(shown.end_date),
            is_current=current is not None and int(current.id) == int(shown.id),
        )
    return TargetLookup(
        status=status,
        telegram_id=int(user.telegram_id),
        first_name=user.first_name,
        username=user.username,
        subscription=subscription,
    )


async def _current_subscription(session: Any, user_id: int, now: datetime) -> Subscription | None:
    return await session.scalar(
        select(Subscription)
        .where(
            Subscription.user_id == user_id,
            Subscription.is_active.is_(True),
            Subscription.end_date >= now,
        )
        .order_by(Subscription.end_date.desc())
        .limit(1)
    )


async def _apply_subscription(
    session: Any,
    *,
    user_id: int,
    plan_id: int,
    duration_days: int,
    mode: str,
    now: datetime,
    current: Subscription | None,
) -> tuple[str, datetime]:
    if mode == ACTION_EXTEND and current is not None:
        await session.execute(
            update(Subscription)
            .where(
                Subscription.user_id == user_id,
                Subscription.is_active.is_(True),
                Subscription.id != current.id,
            )
            .values(is_active=False)
            .execution_options(synchronize_session=False)
        )
        current.plan_id = plan_id
        current.end_date = _naive(current.end_date) + timedelta(days=duration_days)
        return ACTION_EXTEND, current.end_date

    if current is not None:
        current.is_active = False
    await session.execute(
        update(Subscription)
        .where(Subscription.user_id == user_id, Subscription.is_active.is_(True))
        .values(is_active=False)
        .execution_options(synchronize_session=False)
    )
    end_date = now + timedelta(days=duration_days)
    session.add(
        Subscription(
            user_id=user_id,
            plan_id=plan_id,
            start_date=now,
            end_date=end_date,
            is_active=True,
        )
    )
    action = ACTION_REPLACE if mode == ACTION_REPLACE and current is not None else ACTION_GRANT
    return action, end_date


def _add_audit(
    session: Any,
    *,
    admin_telegram_id: int,
    target_user_id: int,
    plan_id: int,
    duration_days: int,
    source: str,
    action: str,
    activation_code_id: int | None,
) -> None:
    session.add(
        PlanGrant(
            admin_telegram_id=admin_telegram_id,
            target_user_id=target_user_id,
            plan_id=plan_id,
            duration_days=duration_days,
            source=source,
            action=action,
            activation_code_id=activation_code_id,
        )
    )


async def _ensure_user(session: Any, telegram_id: int) -> User:
    user = await session.scalar(select(User).where(User.telegram_id == telegram_id))
    if user is not None:
        return user
    user = User(telegram_id=telegram_id, first_name="User", language_code="en")
    session.add(user)
    await session.flush()
    return user


async def _insert_code(
    session: Any,
    *,
    plan_id: int,
    duration_days: int,
    admin_telegram_id: int,
    expires_at: datetime | None,
) -> ActivationCode:
    for _attempt in range(5):
        code = CODE_PREFIX + secrets.token_urlsafe(_CODE_RANDOM_BYTES)
        try:
            async with session.begin_nested():
                row = ActivationCode(
                    code=code,
                    plan_id=plan_id,
                    duration_days=duration_days,
                    created_by_telegram_id=admin_telegram_id,
                    expires_at=expires_at,
                )
                session.add(row)
                await session.flush()
            return row
        except IntegrityError:
            continue
    raise RuntimeError("Could not allocate a unique activation code")


def _status_filter(status: str, now: datetime) -> Any:
    unused = (
        ActivationCode.used_at.is_(None)
        & ActivationCode.revoked_at.is_(None)
        & (ActivationCode.expires_at.is_(None) | (ActivationCode.expires_at > now))
    )
    if status == "unused":
        return unused
    if status == "used":
        return ActivationCode.used_at.is_not(None)
    if status == "revoked":
        return ActivationCode.revoked_at.is_not(None) & ActivationCode.used_at.is_(None)
    return (
        ActivationCode.used_at.is_(None)
        & ActivationCode.revoked_at.is_(None)
        & ActivationCode.expires_at.is_not(None)
        & (ActivationCode.expires_at <= now)
    )


def _code_status(code: ActivationCode, now: datetime) -> str:
    if code.used_at is not None:
        return "used"
    if code.revoked_at is not None:
        return "revoked"
    if code.expires_at is not None and _naive(code.expires_at) <= now:
        return "expired"
    return "unused"


def _terminal_error(code: ActivationCode | None, now: datetime) -> str | None:
    if code is None:
        return ERR_INVALID_CODE
    status = _code_status(code, now)
    if status == "used":
        return ERR_USED
    if status == "revoked":
        return ERR_REVOKED
    if status == "expired":
        return ERR_EXPIRED
    return None


def _like_pattern(term: str) -> str:
    escaped = term.lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _valid_telegram_id(value: int) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and 0 < value <= _MAX_TELEGRAM_ID


def _naive(value: datetime) -> datetime:
    if value.tzinfo is not None:
        return value.astimezone(UTC).replace(tzinfo=None)
    return value


def _iso(value: datetime) -> str:
    return _naive(value).replace(tzinfo=UTC).isoformat()
