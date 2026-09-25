"""Owner and admin command access, and the plain-language feature screens."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

from src.help_text import render_index, render_query
from src.panel import render
from src.plugin_screens import build_plugin_category, build_plugin_detail, build_plugins_home
from src.runtime.actors import IncomingCommandLimit
from src.runtime.flood import AccountLimiter
from src.runtime.plugins import Dispatcher
from src.runtime.store import MAX_ACCOUNT_ADMINS, add_account_admin, list_account_admins


def _use_db(database, tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_FILE", tmp_path / "bot.db")


def _prepare(database):
    database.initialize_database()
    database.update_user_details(SimpleNamespace(id=111, first_name="Owner", username="owner"))
    assert database.add_plan("Admins", 10, 1.0, 30, 5, 10)
    plan_id = database.get_all_plans()[0]["id"]
    assert database.grant_subscription(111, plan_id, 30)[0]
    profile = database.get_random_device_profile()
    assert database.add_managed_account(111, "+15550003333", "session-string", profile["id"])
    account_id = database.get_user_details(111)["accounts"][0]["id"]
    database.set_user_language(111, "ar")
    return account_id


class Sender:
    def __init__(self) -> None:
        self.sent: list[tuple] = []
        self.edits: list[tuple] = []
        self.me = SimpleNamespace(id=5)

    async def send_message(self, chat_id, text, **kwargs):
        self.sent.append((chat_id, text, kwargs.get("reply_to_message_id")))
        return SimpleNamespace(id=20 + len(self.sent), text=text, chat=SimpleNamespace(id=chat_id))

    async def edit_message_text(self, *args, **kwargs):
        self.edits.append((args, kwargs))
        return None


def _message(text, *, outgoing=True, sender_id=5, is_self=None, reply=None):
    if is_self is None:
        is_self = outgoing
    return SimpleNamespace(
        outgoing=outgoing,
        text=text,
        date=datetime.now(UTC),
        id=7,
        chat=SimpleNamespace(id=99, type="private"),
        from_user=SimpleNamespace(id=sender_id, is_self=is_self, is_bot=False, username="ada"),
        reply_to_message=reply,
    )


def _labels(rows):
    return [label for row in rows for label, _data in row]


async def test_owner_and_admin_can_command_and_strangers_cannot(tmp_path, monkeypatch):
    import src.database as database

    _use_db(database, tmp_path, monkeypatch)
    account_id = _prepare(database)
    from src.runtime.actors import incoming_limit

    incoming_limit().reset()
    limiter = AccountLimiter(account_id, min_interval=0, retry_threshold=0)
    sender = Sender()
    dispatcher = Dispatcher()

    assert await dispatcher.handle_message(
        account_id=account_id,
        client=sender,
        message=_message(".فحص"),
        limiter=limiter,
        language="ar",
    )
    assert sender.edits == []
    assert sender.sent[-1][2] == 7
    assert "مللي" in sender.sent[-1][1]

    before = len(sender.sent)
    assert (
        await dispatcher.handle_message(
            account_id=account_id,
            client=sender,
            message=_message(".فحص", outgoing=False, sender_id=8, is_self=False),
            limiter=limiter,
            language="ar",
        )
        is False
    )
    assert len(sender.sent) == before

    assert await dispatcher.handle_message(
        account_id=account_id,
        client=sender,
        message=_message(".فحص", outgoing=False, sender_id=111, is_self=False),
        limiter=limiter,
        language="ar",
    )
    assert sender.edits == []
    assert sender.sent[-1][0] == 99
    assert sender.sent[-1][2] == 7

    assert (
        add_account_admin(account_id, 222, username="helper", added_by_telegram_id=111) == "added"
    )
    assert await dispatcher.handle_message(
        account_id=account_id,
        client=sender,
        message=_message(".ping", outgoing=False, sender_id=222, is_self=False),
        limiter=limiter,
        language="ar",
    )
    assert "مللي" in sender.sent[-1][1]

    from src.runtime.gating import set_account_plugin

    assert set_account_plugin(account_id, 111, "ping") == "disabled"
    before = len(sender.sent)
    assert (
        await dispatcher.handle_message(
            account_id=account_id,
            client=sender,
            message=_message(".فحص", outgoing=False, sender_id=222, is_self=False),
            limiter=limiter,
            language="ar",
        )
        is False
    )
    assert len(sender.sent) == before
    assert set_account_plugin(account_id, 111, "ping") == "enabled"


async def test_admins_cannot_run_owner_commands_or_add_admins(tmp_path, monkeypatch):
    import src.database as database

    _use_db(database, tmp_path, monkeypatch)
    account_id = _prepare(database)
    from src.runtime.actors import incoming_limit

    incoming_limit().reset()
    assert add_account_admin(account_id, 222, username="helper") == "added"
    limiter = AccountLimiter(account_id, min_interval=0, retry_threshold=0)
    sender = Sender()
    dispatcher = Dispatcher()

    assert await dispatcher.handle_message(
        account_id=account_id,
        client=sender,
        message=_message(".اذاعة مرحبا", outgoing=False, sender_id=222, is_self=False),
        limiter=limiter,
        language="ar",
    )
    assert "صاحب الحساب" in sender.sent[-1][1]
    assert sender.edits == []

    before = len(sender.sent)
    assert await dispatcher.handle_message(
        account_id=account_id,
        client=sender,
        message=_message(".رفع ادمن 333", outgoing=False, sender_id=222, is_self=False),
        limiter=limiter,
        language="ar",
    )
    assert "صاحب الحساب" in sender.sent[-1][1]
    assert list_account_admins(account_id) == [
        {"telegram_id": 222, "username": "helper", "added_by_telegram_id": None}
    ]
    assert len(sender.sent) == before + 1

    assert await dispatcher.handle_message(
        account_id=account_id,
        client=sender,
        message=_message(".رفع ادمن 333"),
        limiter=limiter,
        language="ar",
    )
    assert any(row["telegram_id"] == 333 for row in list_account_admins(account_id))

    assert await dispatcher.handle_message(
        account_id=account_id,
        client=sender,
        message=_message(".تنزيل ادمن 333", outgoing=False, sender_id=111, is_self=False),
        limiter=limiter,
        language="ar",
    )
    assert all(row["telegram_id"] != 333 for row in list_account_admins(account_id))
    assert add_account_admin(account_id, 111) == "owner"
    assert add_account_admin(account_id, 5) == "added"


async def test_admin_commands_are_rate_limited(tmp_path, monkeypatch):
    import src.database as database

    _use_db(database, tmp_path, monkeypatch)
    account_id = _prepare(database)
    assert add_account_admin(account_id, 222) == "added"
    limit = IncomingCommandLimit(admin_limit=2, owner_limit=4, account_limit=10, window=30)
    monkeypatch.setattr("src.runtime.actors.incoming_limit", lambda: limit)
    limiter = AccountLimiter(account_id, min_interval=0, retry_threshold=0)
    sender = Sender()
    dispatcher = Dispatcher()
    for _index in range(2):
        assert await dispatcher.handle_message(
            account_id=account_id,
            client=sender,
            message=_message(".فحص", outgoing=False, sender_id=222, is_self=False),
            limiter=limiter,
            language="ar",
        )
    assert await dispatcher.handle_message(
        account_id=account_id,
        client=sender,
        message=_message(".فحص", outgoing=False, sender_id=222, is_self=False),
        limiter=limiter,
        language="ar",
    )
    assert "وقت قصير" in sender.sent[-1][1]
    before = len(sender.sent)
    assert (
        await dispatcher.handle_message(
            account_id=account_id,
            client=sender,
            message=_message(".فحص", outgoing=False, sender_id=222, is_self=False),
            limiter=limiter,
            language="ar",
        )
        is False
    )
    assert len(sender.sent) == before


def test_feature_screens_use_display_names(tmp_path, monkeypatch):
    import src.database as database
    from src.translation import get_translation_func_for_user

    _use_db(database, tmp_path, monkeypatch)
    account_id = _prepare(database)
    tr = get_translation_func_for_user(111)
    home = build_plugins_home(account_id, "ar", tr)
    assert home is not None
    text, rows = home
    labels = _labels(rows)
    assert "اضغط" in text
    assert "ميزات الحساب" in text
    assert "رجوع لهذا الحساب" in " ".join(labels)
    blob = " ".join(labels)
    for raw in ("pmpermit", "codemon", "tagall", "delegates", "ping"):
        assert raw not in blob

    tools = build_plugin_category(account_id, "ar", "tools", tr)
    assert tools is not None
    tool_text, tool_rows = tools
    tool_labels = " ".join(_labels(tool_rows))
    assert "الفحص" in tool_text or "الفحص" in tool_labels
    assert "تعمل الآن" in tool_labels
    assert "رجوع للميزات" in tool_labels
    assert "pmpermit" not in tool_labels

    detail = build_plugin_detail(account_id, "ar", "ping", tr)
    assert detail is not None
    detail_text, detail_rows = detail
    detail_labels = " ".join(_labels(detail_rows))
    assert "الفحص" in detail_text
    assert "إيقاف الفحص" in detail_labels
    assert "ميزات الحساب" in detail_labels
    assert "ping" not in detail_labels

    _panel, panel_rows = render(account_id, 555, "h")
    panel_labels = " ".join(_labels(panel_rows))
    assert "المسؤولون" in panel_labels
    assert "الادارة" in panel_labels or "🛡" in panel_labels
    _section, section_rows = render(account_id, 555, "c", "tools")
    section_labels = " ".join(_labels(section_rows))
    assert "الفحص" in section_labels
    assert "تعمل الآن" in section_labels
    assert "pmpermit" not in section_labels
    plugin_text, plugin_rows = render(account_id, 555, "g", "ping")
    assert "الفحص" in plugin_text
    assert "إيقاف الفحص" in " ".join(_labels(plugin_rows))
    assert "رجوع إلى" in " ".join(_labels(plugin_rows))

    help_text = "\n".join(render_index(account_id, "ar", "."))
    assert "رفع ادمن" not in help_text
    assert "صاحب الحساب" not in help_text
    system = "\n".join(render_query(account_id, "ar", ".", "النظام"))
    assert "رفع ادمن" in system
    assert "صاحب الحساب" in system
    broadcast = "\n".join(render_query(account_id, "ar", ".", "اذاعة"))
    assert "صاحب الحساب" in broadcast
    assert "المسؤولون" not in broadcast
    ping = "\n".join(render_query(account_id, "ar", ".", "فحص"))
    assert "المسؤولون" in ping


def test_admin_cap_and_migration_follow_plan_grants():
    source = Path("alembic/versions/20250925_0006_account_admins.py").read_text(encoding="utf-8")
    assert 'revision: str = "0006_account_admins"' in source
    assert '"0005_plan_grants"' in source
    assert "account_admins" in source
    assert "DELEGATES_PLUGIN_NAMES" in source
    assert MAX_ACCOUNT_ADMINS == 20
