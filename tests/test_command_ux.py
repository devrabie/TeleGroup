"""Reply templates, command help, and the inline panel."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from src.command_catalog import CATEGORY_BY_ID, PLUGIN_CATEGORY
from src.help_text import render_index, render_query
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
    assert ".اللوحة" in arabic
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
    assert "اللوحة" in names
    assert "الاوامر" in names


def test_help_index_is_categorized(tmp_path, monkeypatch):
    import src.database as database

    _use_db(database, tmp_path, monkeypatch)
    account_id, _plan_id = _prepare(database)
    text = render_index(account_id, "ar", ".")
    assert "الادارة" in text
    assert "الحماية" in text
    assert "التحميل" in text
    assert ".فحص" in text
    assert ".ping" in text
    assert ".اللوحة" in text
    detail = render_query(account_id, "ar", ".", "حظر")
    assert "الوصف" in detail
    assert "الاستخدام" in detail
    assert "مثال" in detail
    assert ".حظر" in detail
    section = render_query(account_id, "ar", ".", "الادارة")
    assert ".حظر" in section
    english = render_query(account_id, "en", ".", "ban")
    assert "Description" in english
    assert ".ban" in english


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
                if parsed.op in {"c", "g", "d", "s"} and (parsed.op, parsed.arg) not in walked:
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
