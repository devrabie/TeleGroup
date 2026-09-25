"""Current TeleGroup schema.

Timestamps are stored as naive UTC. Callers that show them to users receive
ISO-8601 strings with a ``+00:00`` offset.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    false,
    func,
    text,
    true,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)
    first_name: Mapped[str] = mapped_column(Text, nullable=False)
    username: Mapped[str | None] = mapped_column(Text)
    is_admin: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=false())
    language_code: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'en'"))
    created_at: Mapped[datetime | None] = mapped_column(DateTime, server_default=func.now())


class Plan(Base):
    __tablename__ = "plans"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    price_stars: Mapped[int] = mapped_column(Integer, nullable=False)
    price_usd: Mapped[float] = mapped_column(Float, nullable=False)
    duration_days: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("30"))
    max_accounts: Mapped[int] = mapped_column(Integer, nullable=False)
    daily_group_limit: Mapped[int] = mapped_column(Integer, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=true())


class Subscription(Base):
    __tablename__ = "subscriptions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    plan_id: Mapped[int] = mapped_column(ForeignKey("plans.id"), nullable=False)
    start_date: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    end_date: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=true())


class Proxy(Base):
    __tablename__ = "proxies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    proxy_string: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    is_working: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=true())
    last_checked: Mapped[datetime | None] = mapped_column(DateTime)


class DeviceProfile(Base):
    __tablename__ = "device_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    device_model: Mapped[str] = mapped_column(Text, nullable=False)
    system_version: Mapped[str] = mapped_column(Text, nullable=False)
    app_version: Mapped[str] = mapped_column(Text, nullable=False)
    lang_code: Mapped[str] = mapped_column(Text, nullable=False)
    client_platform: Mapped[str] = mapped_column(Text, nullable=False)
    api_id: Mapped[int | None] = mapped_column(Integer)
    api_hash: Mapped[str | None] = mapped_column(Text)


class ManagedAccount(Base):
    __tablename__ = "managed_accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    phone: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    session_string: Mapped[str] = mapped_column(Text, nullable=False)
    proxy_id: Mapped[int | None] = mapped_column(ForeignKey("proxies.id"))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=false())
    is_running: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=false())
    code_monitor_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=false()
    )
    flood_wait_until: Mapped[datetime | None] = mapped_column(DateTime)
    next_creation_time: Mapped[datetime | None] = mapped_column(DateTime)
    backoff_level: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    last_error: Mapped[str | None] = mapped_column(Text)
    session_status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'unknown'")
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime | None] = mapped_column(DateTime, server_default=func.now())
    device_profile_id: Mapped[int | None] = mapped_column(ForeignKey("device_profiles.id"))


class GroupCreationLog(Base):
    __tablename__ = "group_creation_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(
        ForeignKey("managed_accounts.id", ondelete="CASCADE"), nullable=False
    )
    group_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    group_name: Mapped[str] = mapped_column(Text, nullable=False)
    creation_timestamp: Mapped[datetime | None] = mapped_column(DateTime, server_default=func.now())


class InfoPage(Base):
    __tablename__ = "info_pages"

    page_key: Mapped[str] = mapped_column(Text, primary_key=True)
    lang_code: Mapped[str] = mapped_column(Text, primary_key=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)


class AccountManager(Base):
    __tablename__ = "account_managers"
    __table_args__ = (
        UniqueConstraint("owner_user_id", "manager_telegram_id", name="uq_account_managers_pair"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    manager_telegram_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[datetime | None] = mapped_column(DateTime, server_default=func.now())


class PlanPlugin(Base):
    """Plugins a subscription plan is allowed to run."""

    __tablename__ = "plan_plugins"

    plan_id: Mapped[int] = mapped_column(
        ForeignKey("plans.id", ondelete="CASCADE"), primary_key=True
    )
    plugin_name: Mapped[str] = mapped_column(String(64), primary_key=True)


class AccountPlugin(Base):
    """Per-account override. Missing rows use the plugin's default."""

    __tablename__ = "account_plugins"

    account_id: Mapped[int] = mapped_column(
        ForeignKey("managed_accounts.id", ondelete="CASCADE"), primary_key=True
    )
    plugin_name: Mapped[str] = mapped_column(String(64), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=true())


class PluginSetting(Base):
    """JSON values scoped to one account and plugin."""

    __tablename__ = "plugin_settings"

    account_id: Mapped[int] = mapped_column(
        ForeignKey("managed_accounts.id", ondelete="CASCADE"), primary_key=True
    )
    plugin_name: Mapped[str] = mapped_column(String(64), primary_key=True)
    setting_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    setting_value: Mapped[str] = mapped_column(Text, nullable=False)


class RuntimeSignal(Base):
    """Wake-up row for the account worker. State is read from the account tables."""

    __tablename__ = "runtime_signals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int | None] = mapped_column(Integer)
    event: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime | None] = mapped_column(DateTime, server_default=func.now())


class SessionLease(Base):
    """Pauses the worker client so the control bot can open the same session."""

    __tablename__ = "session_leases"

    account_id: Mapped[int] = mapped_column(
        ForeignKey("managed_accounts.id", ondelete="CASCADE"), primary_key=True
    )
    holder: Mapped[str] = mapped_column(Text, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    acked: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=false())


class SharingToken(Base):
    __tablename__ = "sharing_tokens"
    __table_args__ = (
        UniqueConstraint("owner_user_id", "kind", name="uq_sharing_tokens_owner_kind"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    token: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    created_at: Mapped[datetime | None] = mapped_column(DateTime, server_default=func.now())
