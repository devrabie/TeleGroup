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

    def test_user_owns_account(self):
        self.database.add_managed_account(111, "+15550003333", "session-string", self.profile["id"])
        acc_id = self.database.get_user_details(111)["accounts"][0]["id"]
        self.assertTrue(self.database.user_owns_account(acc_id, 111))
        self.assertFalse(self.database.user_owns_account(acc_id, 999))
        self.assertFalse(self.database.user_owns_account(999999, 111))

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
        self.assertEqual(t.gettext("Toggle On"), "تفعيل")


class ProxyParseTests(unittest.TestCase):
    def test_build_proxy_dict_parses_host_port_user_pass(self):
        with patch.dict(os.environ, {
            "BOT_TOKEN": "1:test",
            "API_ID": "1",
            "API_HASH": "hash",
            "ADMIN_IDS": "1",
        }, clear=False):
            # Import after env is set so config can load if needed.
            from src.code_monitor import build_proxy_dict
            parsed = build_proxy_dict("10.0.0.1:1080:user:pass")
            self.assertEqual(parsed["scheme"], "socks5")
            self.assertEqual(parsed["hostname"], "10.0.0.1")
            self.assertEqual(parsed["port"], 1080)
            self.assertIn(parsed["username"], ("user", None, os.getenv("PROXY_USERNAME")))

    def test_invalid_proxy_returns_none(self):
        with patch.dict(os.environ, {
            "BOT_TOKEN": "1:test",
            "API_ID": "1",
            "API_HASH": "hash",
            "ADMIN_IDS": "1",
        }, clear=False):
            from src.code_monitor import build_proxy_dict
            self.assertIsNone(build_proxy_dict("not-a-proxy"))


if __name__ == "__main__":
    unittest.main()
