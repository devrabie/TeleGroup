import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("BOT_TOKEN", "1:test")
os.environ.setdefault("API_ID", "1")
os.environ.setdefault("API_HASH", "hash")
os.environ.setdefault("ADMIN_IDS", "1")

from src.security_messages import (
    KIND_LOGIN_CODE,
    KIND_NEW_LOGIN,
    KIND_TELEGRAM_NOTICE,
    KIND_TWO_STEP,
    KIND_VERIFICATION,
    classify_security_message,
    extract_codes,
)


class ClassifySecurityMessageTests(unittest.TestCase):
    def test_official_login_code_is_forwarded(self):
        result = classify_security_message(
            text="Login code: 12345. Do not give this code to anyone, even if they say they are from Telegram!",
            from_user_id=777000,
            username="Telegram",
            is_private=True,
            is_outgoing=False,
        )
        self.assertIsNotNone(result)
        self.assertTrue(result["from_official"])
        self.assertEqual(result["kind"], KIND_LOGIN_CODE)
        self.assertEqual(result["codes"], ["12345"])

    def test_official_two_step_change_is_forwarded(self):
        result = classify_security_message(
            text="Your two-step verification password was changed.",
            from_user_id=777000,
            username="Telegram",
            is_private=True,
            is_outgoing=False,
        )
        self.assertEqual(result["kind"], KIND_TWO_STEP)
        self.assertEqual(result["codes"], [])

    def test_official_new_device_is_forwarded(self):
        result = classify_security_message(
            text="New login. We detected a login into your account from a new device.",
            from_user_id=777000,
            username=None,
            is_private=True,
            is_outgoing=False,
        )
        self.assertEqual(result["kind"], KIND_NEW_LOGIN)

    def test_official_generic_notice_is_forwarded(self):
        result = classify_security_message(
            text="Your Telegram account email was updated.",
            from_user_id=777000,
            username="telegram",
            is_private=True,
            is_outgoing=False,
        )
        self.assertEqual(result["kind"], KIND_TELEGRAM_NOTICE)

    def test_arabic_verification_from_bot_is_forwarded(self):
        result = classify_security_message(
            text="رمز التحقق الخاص بك هو 847291",
            from_user_id=123456,
            username="SomeServiceBot",
            is_private=True,
            is_outgoing=False,
        )
        self.assertEqual(result["kind"], KIND_LOGIN_CODE)
        self.assertEqual(result["codes"], ["847291"])
        self.assertFalse(result["from_official"])

    def test_random_private_numbers_are_ignored(self):
        result = classify_security_message(
            text="hey call me at 12345 later",
            from_user_id=42,
            username="friend",
            is_private=True,
            is_outgoing=False,
        )
        self.assertIsNone(result)

    def test_group_messages_are_ignored(self):
        result = classify_security_message(
            text="Login code: 12345",
            from_user_id=777000,
            username="Telegram",
            is_private=False,
            is_outgoing=False,
        )
        self.assertIsNone(result)

    def test_outgoing_messages_are_ignored(self):
        result = classify_security_message(
            text="Login code: 12345",
            from_user_id=777000,
            username="Telegram",
            is_private=True,
            is_outgoing=True,
        )
        self.assertIsNone(result)

    def test_extract_codes_keeps_order_and_uniques(self):
        self.assertEqual(extract_codes("code 11111 then 22222 then 11111"), ["11111", "22222"])

    def test_verification_keyword_without_code(self):
        result = classify_security_message(
            text="Please confirm the two-step verification method change.",
            from_user_id=999,
            username="SecurityBot",
            is_private=True,
            is_outgoing=False,
        )
        self.assertEqual(result["kind"], KIND_VERIFICATION)


class ManagedAccountDefaultsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "bot.db"
        import src.database as database
        self.database = database
        self.db_patch = patch.object(database, "DB_FILE", self.db_path)
        self.db_patch.start()
        database.initialize_database()

        class FakeUser:
            id = 111
            first_name = "Owner"
            username = "owner"

        database.update_user_details(FakeUser())
        self.profile = database.get_random_device_profile()
        self.assertIsNotNone(self.profile)

    def tearDown(self):
        self.db_patch.stop()
        self.tmp.cleanup()

    def test_new_account_has_both_features_disabled(self):
        added = self.database.add_managed_account(111, "+15551234567", "session-string", self.profile["id"])
        self.assertTrue(added)
        details = self.database.get_user_details(111)
        self.assertEqual(len(details["accounts"]), 1)
        acc = details["accounts"][0]
        self.assertEqual(acc["is_active"], 0)
        self.assertEqual(acc["code_monitor_enabled"], 0)

        full = self.database.get_account_details(acc["id"])
        self.assertEqual(full["is_active"], 0)
        self.assertEqual(full["code_monitor_enabled"], 0)

    def test_toggles_are_independent(self):
        self.database.add_managed_account(111, "+15557654321", "session-string", self.profile["id"])
        acc_id = self.database.get_user_details(111)["accounts"][0]["id"]

        group_on = self.database.toggle_account_status(acc_id, 111)
        self.assertTrue(group_on)
        monitor_on = self.database.toggle_code_monitor(acc_id, 111)
        self.assertTrue(monitor_on)

        acc = self.database.get_account_details(acc_id)
        self.assertEqual(acc["is_active"], 1)
        self.assertEqual(acc["code_monitor_enabled"], 1)

        group_off = self.database.toggle_account_status(acc_id, 111)
        self.assertFalse(group_off)
        acc = self.database.get_account_details(acc_id)
        self.assertEqual(acc["is_active"], 0)
        self.assertEqual(acc["code_monitor_enabled"], 1)

        monitor_off = self.database.toggle_code_monitor(acc_id, 111)
        self.assertFalse(monitor_off)
        acc = self.database.get_account_details(acc_id)
        self.assertEqual(acc["is_active"], 0)
        self.assertEqual(acc["code_monitor_enabled"], 0)

    def test_monitor_toggle_rejects_other_users(self):
        self.database.add_managed_account(111, "+15550001111", "session-string", self.profile["id"])
        acc_id = self.database.get_user_details(111)["accounts"][0]["id"]
        self.assertIsNone(self.database.toggle_code_monitor(acc_id, 999))

    def test_code_monitor_accounts_requires_subscription(self):
        self.database.add_managed_account(111, "+15550002222", "session-string", self.profile["id"])
        acc_id = self.database.get_user_details(111)["accounts"][0]["id"]
        self.database.toggle_code_monitor(acc_id, 111)
        self.assertEqual(self.database.get_code_monitor_accounts(), [])

        self.database.add_plan("Test", 1, 1.0, 30, 5, 10)
        plans = self.database.get_all_plans()
        self.database.grant_subscription(111, plans[0]["id"], 30)
        monitored = self.database.get_code_monitor_accounts()
        self.assertEqual(len(monitored), 1)
        self.assertEqual(monitored[0]["account_id"], acc_id)
        self.assertEqual(monitored[0]["phone"], "+15550002222")

    def test_schema_migration_adds_code_monitor_column(self):
        """Old databases without the column should gain it on initialize."""
        raw_path = Path(self.tmp.name) / "legacy.db"
        conn = sqlite3.connect(raw_path)
        conn.execute("""
            CREATE TABLE managed_accounts (
                id INTEGER PRIMARY KEY,
                user_id INTEGER NOT NULL,
                phone TEXT NOT NULL UNIQUE,
                session_string TEXT NOT NULL,
                is_active BOOLEAN NOT NULL DEFAULT 1
            )
        """)
        conn.commit()
        conn.close()

        with patch.object(self.database, "DB_FILE", raw_path):
            self.database.initialize_database()
            with self.database.get_db_connection() as conn:
                columns = [row[1] for row in conn.execute("PRAGMA table_info(managed_accounts)")]
        self.assertIn("code_monitor_enabled", columns)
        self.assertIn("session_status", columns)

    def test_user_owns_account(self):
        self.database.add_managed_account(111, "+15550003333", "session-string", self.profile["id"])
        acc_id = self.database.get_user_details(111)["accounts"][0]["id"]
        self.assertTrue(self.database.user_owns_account(acc_id, 111))
        self.assertFalse(self.database.user_owns_account(acc_id, 999))
        self.assertFalse(self.database.user_owns_account(999999, 111))

    def test_session_invalid_is_persisted_and_detected(self):
        self.database.add_managed_account(111, "+15550006666", "session-string", self.profile["id"])
        acc_id = self.database.get_user_details(111)["accounts"][0]["id"]
        acc = self.database.get_account_details(acc_id)
        self.assertEqual(acc["session_status"], "ok")
        self.assertFalse(self.database.session_is_invalid(acc))

        self.assertTrue(self.database.looks_like_invalid_session("AUTH_KEY_UNREGISTERED"))
        self.assertTrue(self.database.looks_like_invalid_session("SessionRevoked: The session was revoked"))
        self.assertFalse(self.database.looks_like_invalid_session("SOCKS5 authentication failed"))

        self.database.mark_session_invalid(acc_id, "AUTH_KEY_UNREGISTERED")
        acc = self.database.get_account_details(acc_id)
        self.assertEqual(acc["session_status"], "invalid")
        self.assertTrue(self.database.session_is_invalid(acc))

        from src.account_explorer import format_session_health_text
        banner = format_session_health_text(acc, lambda s: s)
        self.assertIn("Account invalid", banner)
        self.assertIn("sign in again", banner)

        self.database.mark_session_ok(acc_id)
        acc = self.database.get_account_details(acc_id)
        self.assertEqual(acc["session_status"], "ok")
        self.assertFalse(self.database.session_is_invalid(acc))
        self.assertEqual(format_session_health_text(acc, lambda s: s), "")

    def test_new_arabic_telegram_user_gets_arabic_ui(self):
        class ArUser:
            id = 222
            first_name = "Ali"
            username = "ali"
            language_code = "ar-SA"

        self.database.update_user_details(ArUser())
        self.assertEqual(self.database.get_user_language(222), "ar")
        self.assertEqual(self.database.ui_language_from_telegram("ar"), "ar")
        self.assertEqual(self.database.ui_language_from_telegram("en"), "en")
        self.assertEqual(self.database.ui_language_from_telegram(None), "en")

    def test_reassign_proxy_prefers_a_different_proxy(self):
        self.database.batch_insert_proxies(["10.0.0.1:1080:u:p", "10.0.0.2:1080:u:p"])
        self.database.add_managed_account(111, "+15550004444", "session-string", self.profile["id"])
        acc_id = self.database.get_user_details(111)["accounts"][0]["id"]
        before = self.database.get_account_details(acc_id)
        self.assertIsNotNone(before["proxy_id"])

        seen = set()
        for _ in range(8):
            ok, key = self.database.reassign_proxy(acc_id, 111)
            self.assertTrue(ok)
            self.assertEqual(key, "proxy_update_success")
            seen.add(self.database.get_account_details(acc_id)["proxy_id"])
        self.assertGreaterEqual(len(seen), 2)

    def test_batch_insert_reactivates_marked_bad_proxies(self):
        self.database.batch_insert_proxies(["10.0.0.8:1080:u:p"])
        with self.database.get_db_connection() as conn:
            proxy_id = conn.execute(
                "SELECT id FROM proxies WHERE proxy_string = ?",
                ("10.0.0.8:1080:u:p",),
            ).fetchone()["id"]
        self.database.mark_proxy_as_bad(proxy_id)
        self.assertEqual(self.database.count_working_proxies(), 0)

        self.database.batch_insert_proxies(["10.0.0.8:1080:u:p"])
        self.assertEqual(self.database.count_working_proxies(), 1)

    def test_rotate_account_proxy_marks_failed_and_switches(self):
        self.database.batch_insert_proxies(["10.0.0.1:1080:u:p", "10.0.0.2:1080:u:p"])
        self.database.add_managed_account(111, "+15550005555", "session-string", self.profile["id"])
        acc_id = self.database.get_user_details(111)["accounts"][0]["id"]
        failed_id = self.database.get_account_details(acc_id)["proxy_id"]
        self.assertIsNotNone(failed_id)

        new_id = self.database.rotate_account_proxy(acc_id, failed_id)
        self.assertIsNotNone(new_id)
        self.assertNotEqual(new_id, failed_id)
        self.assertEqual(self.database.get_account_details(acc_id)["proxy_id"], new_id)

        with self.database.get_db_connection() as conn:
            row = conn.execute("SELECT is_working FROM proxies WHERE id = ?", (failed_id,)).fetchone()
        self.assertEqual(row["is_working"], 0)


class TwoStepPasswordValidationTests(unittest.IsolatedAsyncioTestCase):
    def test_validate_two_step_password(self):
        from src.two_step import validate_two_step_password
        self.assertEqual(validate_two_step_password(""), "empty")
        self.assertEqual(validate_two_step_password(None), "empty")
        self.assertEqual(validate_two_step_password("abc"), "too_short")
        self.assertEqual(validate_two_step_password("abcd"), None)
        self.assertEqual(validate_two_step_password("pass\nword"), "invalid")
        self.assertEqual(validate_two_step_password("x" * 257), "too_long")

    async def test_apply_rejects_short_password_without_connecting(self):
        from src.two_step import TwoStepError, apply_two_step_password
        with self.assertRaises(TwoStepError) as ctx:
            await apply_two_step_password(1, "ab")
        self.assertEqual(ctx.exception.code, "too_short")


class TranslationCatalogTests(unittest.TestCase):
    def setUp(self):
        import src.translation as translation
        self.translation = translation
        translation._translation_cache.clear()
        translation._compiled = False

    def test_arabic_feature_strings_are_loaded_after_compile(self):
        compiled = self.translation.compile_translations(force=True)
        self.assertGreaterEqual(compiled, 1)
        t = self.translation.get_translator("ar")
        self.assertEqual(t.gettext("🔑 Two-Step Verification"), "🔑 رمز التحقق بخطوتين")
        self.assertEqual(t.gettext("🔐 Enable Code Monitor"), "🔐 تفعيل مراقبة الأكواد")
        self.assertEqual(t.gettext("▶️ Enable Group Creation"), "▶️ تفعيل إنشاء المجموعات")
        self.assertEqual(t.gettext("👤 View Profile"), "👤 الملف الشخصي")
        self.assertEqual(t.gettext("💬 Private Chats"), "💬 المحادثات الخاصة")
        self.assertEqual(t.gettext("📊 Group Report"), "📊 تقرير المجموعات")
        self.assertEqual(t.gettext("⚠️ <b>Account invalid</b>"), "⚠️ <b>الحساب غير صالح</b>")
        self.assertEqual(t.gettext("Toggle On"), "تفعيل")


class MessageEditHelperTests(unittest.TestCase):
    def test_is_message_not_modified(self):
        from telegram.error import BadRequest
        from src.user_handlers import _is_message_not_modified

        self.assertTrue(
            _is_message_not_modified(
                BadRequest("Message is not modified: specified new message content and reply markup are exactly the same")
            )
        )
        self.assertFalse(_is_message_not_modified(BadRequest("Message to edit not found")))
        self.assertFalse(_is_message_not_modified(ValueError("other")))

    def test_is_stale_callback(self):
        from telegram.error import BadRequest
        from src.user_handlers import _is_stale_callback

        self.assertTrue(
            _is_stale_callback(
                BadRequest("Query is too old and response timeout expired or query id is invalid")
            )
        )
        self.assertFalse(_is_stale_callback(BadRequest("Message to edit not found")))
        self.assertFalse(_is_stale_callback(ValueError("other")))


class ProxyParseTests(unittest.TestCase):
    def test_build_proxy_dict_parses_host_port_user_pass(self):
        from src.code_monitor import build_proxy_dict
        with patch("src.code_monitor.config.PROXY_USERNAME", None), patch(
            "src.code_monitor.config.PROXY_PASSWORD", None
        ):
            parsed = build_proxy_dict("10.0.0.1:1080:user:pass")
        self.assertEqual(parsed["scheme"], "socks5")
        self.assertEqual(parsed["hostname"], "10.0.0.1")
        self.assertEqual(parsed["port"], 1080)
        self.assertEqual(parsed["username"], "user")
        self.assertEqual(parsed["password"], "pass")

    def test_string_credentials_win_over_env(self):
        from src.code_monitor import build_proxy_dict
        with patch("src.code_monitor.config.PROXY_USERNAME", "envuser"), patch(
            "src.code_monitor.config.PROXY_PASSWORD", "envpass"
        ):
            parsed = build_proxy_dict("10.0.0.1:1080:user:pass")
        self.assertEqual(parsed["username"], "user")
        self.assertEqual(parsed["password"], "pass")

    def test_env_used_only_when_string_has_no_auth(self):
        from src.code_monitor import build_proxy_dict
        with patch("src.code_monitor.config.PROXY_USERNAME", "envuser"), patch(
            "src.code_monitor.config.PROXY_PASSWORD", "envpass"
        ):
            parsed = build_proxy_dict("10.0.0.1:1080")
        self.assertEqual(parsed["username"], "envuser")
        self.assertEqual(parsed["password"], "envpass")

    def test_user_pass_at_host_and_url_formats(self):
        from src.code_monitor import build_proxy_dict
        with patch("src.code_monitor.config.PROXY_USERNAME", None), patch(
            "src.code_monitor.config.PROXY_PASSWORD", None
        ):
            at_form = build_proxy_dict("user:p:ass@10.0.0.1:1080")
            url_form = build_proxy_dict("socks5://user:pass@10.0.0.1:1080")
            colon_pass = build_proxy_dict("10.0.0.1:1080:user:p:ass")
        self.assertEqual(at_form["username"], "user")
        self.assertEqual(at_form["password"], "p:ass")
        self.assertEqual(url_form["username"], "user")
        self.assertEqual(url_form["password"], "pass")
        self.assertEqual(colon_pass["password"], "p:ass")

    def test_invalid_proxy_returns_none(self):
        from src.code_monitor import build_proxy_dict
        self.assertIsNone(build_proxy_dict("not-a-proxy"))
        self.assertIsNone(build_proxy_dict(""))
        self.assertIsNone(build_proxy_dict(None))

    def test_is_socks_auth_error(self):
        from src.code_monitor import is_socks_auth_error
        self.assertTrue(is_socks_auth_error(OSError("Socket error: SOCKS5 authentication failed")))
        wrapped = ConnectionError("Unable to connect")
        wrapped.__cause__ = OSError("Socket error: SOCKS5 authentication failed")
        self.assertTrue(is_socks_auth_error(wrapped))
        self.assertFalse(is_socks_auth_error(ConnectionError("Unable to connect")))
        self.assertFalse(is_socks_auth_error(TimeoutError("timed out")))
        self.assertFalse(is_socks_auth_error(OSError("Network is unreachable")))


class AccountExplorerFormatTests(unittest.TestCase):
    def test_display_name_and_gift_line(self):
        from src.account_explorer import display_name, format_gift_line

        self.assertEqual(display_name(None, "+1555"), "+1555")
        self.assertEqual(display_name({"full_name": "Ali Ahmad"}, "+1555"), "Ali Ahmad")
        line = format_gift_line({
            "title": "Plush Pepe",
            "number": 1234,
            "is_upgraded": True,
            "is_pinned": True,
            "from_name": "Sara",
        })
        self.assertIn("💎", line)
        self.assertIn("Plush Pepe", line)
        self.assertIn("#1234", line)
        self.assertIn("Sara", line)

    def test_dialog_button_and_message_line(self):
        from src.account_explorer import format_dialog_button, format_message_line

        button = format_dialog_button(
            {"name": "Omar", "unread": 3, "is_bot": False, "is_self": False, "is_official": False},
            "Saved Messages",
        )
        self.assertTrue(button.startswith("🔵"))
        self.assertIn("Omar", button)
        self.assertIn("(3)", button)

        saved = format_dialog_button(
            {"name": "me", "unread": 0, "is_self": True, "is_bot": False, "is_official": False},
            "💾 Saved Messages",
        )
        self.assertIn("Saved Messages", saved)

        line = format_message_line(
            {
                "outgoing": True,
                "text": "hello <world>",
                "date": "2026-09-07T18:06:42",
                "from_name": "Ali",
            },
            lambda s: s,
            "You",
        )
        self.assertIn("→", line)
        self.assertIn("You", line)
        self.assertIn("hello &lt;world&gt;", line)

    def test_paginate_and_profile_text(self):
        from src.account_explorer import format_profile_text, paginate

        items, page, pages = paginate(list(range(13)), 1, 6)
        self.assertEqual(items, [6, 7, 8, 9, 10, 11])
        self.assertEqual(page, 1)
        self.assertEqual(pages, 3)

        text = format_profile_text(
            {
                "identity": {
                    "full_name": "Ali",
                    "username": "ali",
                    "phone": "+1555123",
                    "user_id": 99,
                    "is_premium": True,
                    "language_code": "ar",
                    "bio": "Hello <b>",
                    "collectible_usernames": ["ali.nft"],
                },
                "gifts": [
                    {"title": "Rose", "is_upgraded": False, "from_name": "Omar"},
                ],
                "total_gifts": 4,
            },
            "+1555123",
            lambda s: s,
        )
        self.assertIn("Ali", text)
        self.assertIn("@ali", text)
        self.assertIn("99", text)
        self.assertIn("Telegram Premium", text)
        self.assertIn("@ali.nft", text)
        self.assertIn("Hello &lt;b&gt;", text)
        self.assertIn("Rose", text)
        self.assertIn("…and 3 more", text)

    def test_memory_cache_roundtrip(self):
        from src.cache_store import CacheStore

        store = CacheStore()
        store.set_json("unit-test-key", {"ok": True}, ttl_seconds=30)
        self.assertEqual(store.get_json("unit-test-key"), {"ok": True})
        store.delete("unit-test-key")
        self.assertIsNone(store.get_json("unit-test-key"))

    def test_is_recent_enough_for_code_catch_up(self):
        from datetime import datetime, timedelta, timezone
        from src.code_monitor import is_recent_enough

        now = datetime.now(timezone.utc)
        self.assertTrue(is_recent_enough(now - timedelta(minutes=10)))
        self.assertFalse(is_recent_enough(now - timedelta(hours=7)))
        self.assertFalse(is_recent_enough(None))


if __name__ == "__main__":
    unittest.main()
