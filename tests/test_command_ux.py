"""Reply templates, command help, and the inline panel."""

from __future__ import annotations

import logging
import re
from types import SimpleNamespace

import pytest
from src.command_catalog import CATEGORY_BY_ID, PLUGIN_CATEGORY
from src.help_text import pack_help_pages, render_index, render_query, telegram_units
from src.panel import (
    actor_allowed,
    adjust_setting,
    pack_callback,
    pack_query,
    render,
    toggle_plugin,
    unpack_callback,
    unpack_query,
)
from src.panel_bot import on_panel_callback
from src.panel_open import open_panel
from src.runtime.flood import AccountLimiter
from src.runtime.plugins import CommandContext, all_plugins, load_plugins
from src.templates import SEP, guess_tone, inline_disabled, tone_line


def _use_db(database, tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_FILE", tmp_path / "bot.db")


def _prepare(database) -> tuple[int, int]:
    database.initialize_database()
    database.update_user_details(SimpleNamespace(id=111, first_name="Owner", username="owner"))
    assert database.add_plan("Panel", 10, 1.0, 30, 5, 10)
    plan_id = database.get_all_plans()[0]["id"]
    assert database.grant_subscription(111, plan_id, 30)[0]
    profile = database.get_random_device_profile()
    assert database.add_managed_account(111, "+15550002222", "session-string", profile["id"])
    account_id = database.get_user_details(111)["accounts"][0]["id"]
    database.set_user_language(111, "ar")
    return account_id, plan_id


def test_tone_line_frames_arabic_and_english():
    arabic = tone_line("ar", "ok", "تم الحظر.")
    english = tone_line("en", "err", "Telegram refused that action.")
    assert arabic.startswith("𓆩 ✅ تم")
    assert SEP in arabic
    assert arabic.endswith("تم الحظر.")
    assert "❌" in english
    assert english.endswith("Telegram refused that action.")
    assert guess_tone("Downloading…") == "wait"
    assert guess_tone("Could not convert that file.") == "err"
    assert guess_tone("Banned.") == "ok"


def test_inline_disabled_tells_the_owner_to_use_botfather():
    arabic = inline_disabled("ar", ".")
    english = inline_disabled("en", ".")
    assert "/setinline" in arabic
    assert "BotFather" in arabic
    assert ".تحكم" in arabic
    assert ".اللوحة" not in arabic
    assert "/setinline" in english
    assert ".panel" in english


def test_every_command_has_help_and_a_category():
    load_plugins()
    names = set()
    for plugin in all_plugins():
        assert plugin.meta.name in PLUGIN_CATEGORY
        assert PLUGIN_CATEGORY[plugin.meta.name] in CATEGORY_BY_ID
        assert plugin.meta.description_ar.strip()
        assert plugin.meta.description_en.strip()
        for command in plugin.meta.commands:
            assert command.name not in names
            names.add(command.name)
            assert command.description_ar.strip()
            assert command.description_en.strip()
            assert command.usage_ar.strip()
            assert command.usage_en.strip()
            assert "{p}" in command.usage_ar
            assert command.example_ar.strip()
            assert command.example_en.strip()
    assert "فحص" in names
    assert "تحكم" in names
    assert "اللوحة" in names
    assert "الاوامر" in names


def _joined(pages: list[str]) -> str:
    assert pages
    assert all(telegram_units(page) <= 4096 for page in pages)
    return "\n".join(pages)


def test_help_index_is_categorized(tmp_path, monkeypatch):
    import src.database as database

    _use_db(database, tmp_path, monkeypatch)
    account_id, _plan_id = _prepare(database)
    text = _joined(render_index(account_id, "ar", "."))
    assert "الادارة" in text
    assert "الحماية" in text
    assert "التحميل" in text
    assert re.search(r"1\. 🛡 الادارة ← \.الاوامر الادارة \(\d+ ", text)
    assert ".تحكم" in text
    assert "صاحب الحساب" not in text
    assert ".فحص" not in text
    assert ".ping" not in text
    assert ".اللوحة" not in text
    detail = _joined(render_query(account_id, "ar", ".", "حظر"))
    assert "الوصف" in detail
    assert "الاستخدام" in detail
    assert "مثال" in detail
    assert ".حظر" in detail
    assert ".ban" in detail
    assert "الحالة" in detail
    assert "من يستخدمه" in detail
    section = _joined(render_query(account_id, "ar", ".", "الادارة"))
    assert "● .حظر ←" in section
    assert "   الاستخدام:" in section
    assert ".ban" not in section
    assert "\n\n" in section
    guard = _joined(render_query(account_id, "ar", ".", "الحماية"))
    assert "أكواد الدخول" in guard
    assert "إعادة توجيه أكواد الدخول" in guard
    assert "الاستخدام:" in guard
    english_index = _joined(render_index(account_id, "en", "."))
    assert ".help management" in english_index
    assert ".panel" in english_index
    assert ".حظر" not in english_index
    english_section = _joined(render_query(account_id, "en", ".", "management"))
    assert "● .ban ←" in english_section
    assert "   Usage:" in english_section
    assert ".حظر" not in english_section
    english = _joined(render_query(account_id, "en", ".", "ban"))
    assert "Description" in english
    assert ".ban" in english
    assert "Who can use it" in english
    system = _joined(render_query(account_id, "ar", ".", "النظام"))
    assert "صاحب الحساب" in system
    assert ".رفع ادمن" in system
    delegates = _joined(render_query(account_id, "ar", ".", "المسؤولين"))
    assert "من يرسل الأوامر" in delegates
    assert ".تحكم" in _joined(render_query(account_id, "ar", ".", "تحكم"))


def test_help_pages_stay_under_the_telegram_limit():
    title = "الادارة 🛡"
    blocks = [f"الإشراف\n● .حظر ← وصف {index}\n   الاستخدام: .حظر" for index in range(80)]
    pages = pack_help_pages("ar", title, blocks, limit=500)
    assert len(pages) > 1
    assert all(telegram_units(page) <= 500 for page in pages)
    joined = "\n".join(pages)
    assert "● .حظر ← وصف 0" in joined
    assert "● .حظر ← وصف 79" in joined


def test_callback_data_stays_within_64_bytes(tmp_path, monkeypatch):
    import src.database as database

    _use_db(database, tmp_path, monkeypatch)
    account_id, _plan_id = _prepare(database)
    account_user_id = 10**12
    huge = pack_callback(2**31, 2**52, "k", "telegraph:3:m")
    assert len(huge.encode("utf-8")) <= 64
    unpacked = unpack_callback(huge)
    assert unpacked is not None
    assert unpacked.op == "k"
    assert unpacked.arg == "telegraph:3:m"
    tampered = huge[:-1] + ("0" if huge[-1] != "0" else "1")
    assert unpack_callback(tampered) is None

    query = pack_query(account_id, account_user_id)
    assert unpack_query(query) == (account_id, account_user_id)
    assert unpack_query(query + "x") is None

    seen: list[str] = []

    def _walk(op: str, arg: str) -> None:
        _text, rows = render(account_id, account_user_id, op, arg)
        for row in rows:
            for _label, data in row:
                assert len(data.encode("utf-8")) <= 64
                parsed = unpack_callback(data)
                assert parsed is not None
                assert parsed.account_id == account_id
                assert parsed.account_user_id == account_user_id
                seen.append(data)
                if parsed.op in {"c", "g", "d", "s", "a"} and (parsed.op, parsed.arg) not in walked:
                    walked.add((parsed.op, parsed.arg))
                    _walk(parsed.op, parsed.arg)

    walked: set[tuple[str, str]] = set()
    _walk("h", "")
    assert seen
    assert any(unpack_callback(item).op == "t" for item in seen if unpack_callback(item))


def test_panel_authorization_and_toggle(tmp_path, monkeypatch):
    import src.database as database
    from src.runtime.gating import plugin_is_enabled
    from src.runtime.plugins import get_plugin
    from src.runtime.settings_form import current_setting

    _use_db(database, tmp_path, monkeypatch)
    account_id, plan_id = _prepare(database)
    owner_id = 111
    account_user_id = 555
    stranger = 999
    assert actor_allowed(account_id, owner_id, account_user_id)
    assert actor_allowed(account_id, account_user_id, account_user_id)
    assert not actor_allowed(account_id, stranger, account_user_id)

    ping = get_plugin("ping")
    assert ping is not None
    assert plugin_is_enabled(account_id, ping)
    assert toggle_plugin(account_id, stranger, account_user_id, "ping") == "denied"
    assert plugin_is_enabled(account_id, ping)
    assert toggle_plugin(account_id, account_user_id, account_user_id, "ping") == "disabled"
    assert not plugin_is_enabled(account_id, ping)
    assert toggle_plugin(account_id, owner_id, account_user_id, "ping") == "enabled"

    from src.runtime.store import set_plan_plugin_allowed

    assert set_plan_plugin_allowed(plan_id, "calc", False)
    assert toggle_plugin(account_id, owner_id, account_user_id, "calc") == "locked"

    pm = get_plugin("pmpermit")
    assert pm is not None
    assert adjust_setting(account_id, stranger, account_user_id, "pmpermit", 0, "t") == "denied"
    assert current_setting(account_id, "pmpermit", pm.meta.settings[0]) is False
    assert adjust_setting(account_id, owner_id, account_user_id, "pmpermit", 0, "t") == "saved"
    assert current_setting(account_id, "pmpermit", pm.meta.settings[0]) is True
    assert adjust_setting(account_id, owner_id, account_user_id, "pmpermit", 1, "p") == "saved"
    assert current_setting(account_id, "pmpermit", pm.meta.settings[1]) == 4
    assert adjust_setting(account_id, owner_id, account_user_id, "pmpermit", 2, "t") == "readonly"


@pytest.mark.asyncio
async def test_open_panel_reports_inline_disabled(monkeypatch):
    class Disabled(Exception):
        pass

    Disabled.__name__ = "BotInlineDisabled"

    class Client:
        me = SimpleNamespace(id=5)

        async def get_inline_bot_results(self, username, query):
            assert username == "controlbot"
            assert query.startswith("tg.")
            raise Disabled("BOT_INLINE_DISABLED")

        async def send_inline_bot_result(self, *args):
            raise AssertionError("should not send")

    async def username():
        return "controlbot"

    monkeypatch.setattr("src.panel_open.control_bot_username", username)
    ctx = CommandContext(
        client=Client(),
        account_id=7,
        message=SimpleNamespace(id=3, chat=SimpleNamespace(id=9)),
        command="اللوحة",
        args="",
        prefix=".",
        language="ar",
        plugin_name="help",
        limiter=AccountLimiter(7, min_interval=0, retry_threshold=0),
    )
    assert await open_panel(ctx) == "disabled"


class _Callback:
    def __init__(self, data: str, *, message: object | None, inline_message_id: str | None):
        self.data = data
        self.from_user = SimpleNamespace(id=111)
        self.message = message
        self.inline_message_id = inline_message_id
        self.edits: list[tuple[str, dict]] = []
        self.answers: list[tuple[object, bool]] = []

    async def edit_message_text(self, text: str, **kwargs: object) -> None:
        self.edits.append((text, kwargs))

    async def answer(self, text: object = None, show_alert: bool = False) -> None:
        self.answers.append((text, show_alert))


async def _press(
    data: str,
    *,
    message: object | None = None,
    inline_message_id: str | None = "inline-1",
) -> _Callback:
    query = _Callback(data, message=message, inline_message_id=inline_message_id)
    await on_panel_callback(SimpleNamespace(callback_query=query), None)
    return query


def _button(rows: list, op: str, arg: str | None = None) -> str:
    for row in rows:
        for _label, data in row:
            parsed = unpack_callback(data)
            assert parsed is not None
            if parsed.op == op and (arg is None or parsed.arg == arg):
                return data
    raise AssertionError(f"missing {op} {arg}")


@pytest.mark.asyncio
async def test_inline_callbacks_edit_when_message_is_missing(tmp_path, monkeypatch, caplog):
    import src.database as database
    from src.runtime.store import add_account_admin

    _use_db(database, tmp_path, monkeypatch)
    account_id, _plan_id = _prepare(database)
    account_user_id = 555
    assert add_account_admin(account_id, 222, username="helper") == "added"

    async def edited(op: str, arg: str = "") -> _Callback:
        query = await _press(pack_callback(account_id, account_user_id, op, arg))
        assert query.edits, op
        text, kwargs = query.edits[-1]
        assert text
        assert kwargs["reply_markup"] is not None
        assert kwargs["parse_mode"]
        return query

    home = await edited("h")
    home_text, home_markup = home.edits[-1]
    assert "لوحة التحكم" in home_text
    assert home_markup["reply_markup"].inline_keyboard

    _section_text, section_rows = render(account_id, account_user_id, "c", "admin")
    back = await _press(_button(section_rows, "h"))
    assert back.edits
    assert "لوحة التحكم" in back.edits[-1][0]

    category = await edited("c", "admin")
    assert "الادارة" in category.edits[-1][0] or "🛡" in category.edits[-1][0]

    plugin = await edited("g", "ping")
    assert "الفحص" in plugin.edits[-1][0]

    _plugin_text, plugin_rows = render(account_id, account_user_id, "g", "ping")
    detail_data = _button(plugin_rows, "d")
    detail = await _press(detail_data)
    assert detail.edits
    assert "الاستخدام" in detail.edits[-1][0]

    toggled = await edited("t", "ping")
    assert toggled.answers
    assert toggled.answers[-1][1] is True

    settings = await edited("s", "pmpermit")
    assert "إعداد" in settings.edits[-1][0] or "⚙️" in settings.edits[-1][0]
    _settings_text, settings_rows = render(account_id, account_user_id, "s", "pmpermit")
    adjusted = await _press(_button(settings_rows, "k"))
    assert adjusted.edits
    assert adjusted.answers[-1][1] is True

    admins = await edited("a")
    assert "المسؤول" in admins.edits[-1][0]
    _admins_text, admin_rows = render(account_id, account_user_id, "a", "")
    removed = await _press(_button(admin_rows, "r"))
    assert removed.edits
    assert "لا يوجد مسؤولون" in removed.edits[-1][0]

    closed = await edited("x")
    assert "أُغلقت" in closed.edits[-1][0]
    assert closed.edits[-1][1]["reply_markup"].inline_keyboard == ()

    posted = await _press(
        pack_callback(account_id, account_user_id, "h"),
        message=SimpleNamespace(chat_id=1, message_id=2),
        inline_message_id=None,
    )
    assert posted.edits

    nowhere = await _press(
        pack_callback(account_id, account_user_id, "h"),
        message=None,
        inline_message_id=None,
    )
    assert nowhere.edits == []
    assert nowhere.answers

    class Broken(_Callback):
        async def edit_message_text(self, text: str, **kwargs: object) -> None:
            raise RuntimeError("edit rejected")

    broken = Broken(
        pack_callback(account_id, account_user_id, "c", "tools"),
        message=None,
        inline_message_id="inline-2",
    )
    with caplog.at_level(logging.WARNING, logger="src.panel_bot"):
        await on_panel_callback(SimpleNamespace(callback_query=broken), None)
    assert broken.answers
    assert any("Panel edit failed" in record.message for record in caplog.records)
    assert any(record.exc_info for record in caplog.records)
