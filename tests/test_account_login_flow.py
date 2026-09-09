import asyncio
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("BOT_TOKEN", "1:test")
os.environ.setdefault("API_ID", "1")
os.environ.setdefault("API_HASH", "hash")
os.environ.setdefault("ADMIN_IDS", "1")

from telegram.ext import ConversationHandler
from pyrogram.errors import SessionPasswordNeeded, PhoneCodeInvalid, PasswordHashInvalid
from src.user_handlers import (
    PHONE, CODE, PASSWORD,
    receive_phone_number,
    receive_phone_code,
    receive_password,
    send_login_code,
)
from src.database import get_random_proxy_id, get_db_connection


class AddAccountLoginFlowTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.update = MagicMock()
        self.update.effective_user.id = 123456
        self.update.message = MagicMock()
        self.update.message.text = ""
        self.update.message.reply_text = AsyncMock()
        
        self.context = MagicMock()
        self.context.user_data = {}
        self.context.bot = MagicMock()
        self.context.bot.send_message = AsyncMock()

    async def test_invalid_phone_number_stays_in_phone_state(self):
        self.update.message.text = "invalid-phone"
        state = await receive_phone_number(self.update, self.context)
        self.assertEqual(state, PHONE)
        self.assertNotIn("phone_code_hash", self.context.user_data)

    @patch("src.user_handlers.send_login_code", new_callable=AsyncMock)
    async def test_send_login_code_failure_stays_in_phone_state(self, mock_send):
        mock_send.return_value = (False, "connection_failed", None)
        self.update.message.text = "+1234567890"
        
        status_msg = MagicMock()
        status_msg.edit_text = AsyncMock()
        self.update.message.reply_text.return_value = status_msg

        state = await receive_phone_number(self.update, self.context)
        self.assertEqual(state, PHONE)
        self.assertNotIn("phone_code_hash", self.context.user_data)
        status_msg.edit_text.assert_awaited()

    @patch("src.user_handlers.send_login_code", new_callable=AsyncMock)
    async def test_send_login_code_success_moves_to_code_state(self, mock_send):
        async def side_effect(phone, context, user_id, _):
            context.user_data["phone_code_hash"] = "test_hash"
            return True, None, None

        mock_send.side_effect = side_effect
        self.update.message.text = "+1234567890"

        status_msg = MagicMock()
        status_msg.edit_text = AsyncMock()
        self.update.message.reply_text.return_value = status_msg

        state = await receive_phone_number(self.update, self.context)
        self.assertEqual(state, CODE)
        self.assertEqual(self.context.user_data.get("phone_code_hash"), "test_hash")

    async def test_phone_code_without_hash_does_not_raise_key_error(self):
        self.update.message.text = "12345"
        self.context.user_data = {}
        state = await receive_phone_code(self.update, self.context)
        self.assertEqual(state, PHONE)
        self.update.message.reply_text.assert_awaited()

    @patch("src.user_handlers.receive_phone_number", new_callable=AsyncMock)
    async def test_phone_number_sent_in_code_state_redirects_to_receive_phone_number(self, mock_phone_handler):
        mock_phone_handler.return_value = PHONE
        self.update.message.text = "+19876543210"
        self.context.user_data = {}

        state = await receive_phone_code(self.update, self.context)
        self.assertEqual(state, PHONE)
        mock_phone_handler.assert_awaited_once_with(self.update, self.context)

    async def test_phone_code_invalid_stays_in_code_state(self):
        client = MagicMock()
        client.is_connected = True
        client.sign_in = AsyncMock(side_effect=PhoneCodeInvalid())

        self.context.user_data = {
            "pyrogram_client": client,
            "phone_code_hash": "hash_123",
            "phone": "+1234567890",
        }
        self.update.message.text = "99999"
        status_msg = MagicMock()
        status_msg.edit_text = AsyncMock()
        self.update.message.reply_text.return_value = status_msg

        state = await receive_phone_code(self.update, self.context)
        self.assertEqual(state, CODE)

    async def test_phone_code_requires_2fa_moves_to_password_state(self):
        client = MagicMock()
        client.is_connected = True
        client.sign_in = AsyncMock(side_effect=SessionPasswordNeeded())

        self.context.user_data = {
            "pyrogram_client": client,
            "phone_code_hash": "hash_123",
            "phone": "+1234567890",
        }
        self.update.message.text = "12345"
        status_msg = MagicMock()
        status_msg.edit_text = AsyncMock()
        self.update.message.reply_text.return_value = status_msg

        state = await receive_phone_code(self.update, self.context)
        self.assertEqual(state, PASSWORD)

    @patch("src.user_handlers.async_complete_login", new_callable=AsyncMock)
    async def test_phone_code_success_ends_conversation(self, mock_complete):
        client = MagicMock()
        client.is_connected = True
        client.sign_in = AsyncMock()

        self.context.user_data = {
            "pyrogram_client": client,
            "phone_code_hash": "hash_123",
            "phone": "+1234567890",
        }
        self.update.message.text = "12345"
        status_msg = MagicMock()
        status_msg.edit_text = AsyncMock()
        self.update.message.reply_text.return_value = status_msg

        state = await receive_phone_code(self.update, self.context)
        self.assertEqual(state, ConversationHandler.END)
        mock_complete.assert_awaited_once()

    async def test_password_invalid_stays_in_password_state(self):
        client = MagicMock()
        client.is_connected = True
        client.check_password = AsyncMock(side_effect=PasswordHashInvalid())

        self.context.user_data = {
            "pyrogram_client": client,
        }
        self.update.message.text = "wrong_password"
        status_msg = MagicMock()
        status_msg.edit_text = AsyncMock()
        self.update.message.reply_text.return_value = status_msg

        state = await receive_password(self.update, self.context)
        self.assertEqual(state, PASSWORD)

    @patch("src.user_handlers.async_complete_login", new_callable=AsyncMock)
    async def test_password_success_ends_conversation(self, mock_complete):
        client = MagicMock()
        client.is_connected = True
        client.check_password = AsyncMock()

        self.context.user_data = {
            "pyrogram_client": client,
        }
        self.update.message.text = "correct_password"
        status_msg = MagicMock()
        status_msg.edit_text = AsyncMock()
        self.update.message.reply_text.return_value = status_msg

        state = await receive_password(self.update, self.context)
        self.assertEqual(state, ConversationHandler.END)
        mock_complete.assert_awaited_once()


class ProxySelectionTests(unittest.TestCase):
    def test_get_random_proxy_id_excludes_set_or_list(self):
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("CREATE TABLE IF NOT EXISTS proxies (id INTEGER PRIMARY KEY AUTOINCREMENT, proxy_string TEXT, is_working INTEGEQ DEFAULT 1, last_checked TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
            cursor.execute("DELETE FROM proxies")
            cursor.execute("INSERT INTO proxies (id, proxy_string, is_working) VALUES (101, 'proxy1:8080', 1)")
            cursor.execute("INSERT INTO proxies (id, proxy_string, is_working) VALUES (102, 'proxy2:8080', 1)")
            cursor.execute("INSERT INTO proxies (id, proxy_string, is_working) VALUES (103, 'proxy3:8080', 1)")
            conn.commit()

        selected = get_random_proxy_id(exclude_id={101, 102})
        self.assertEqual(selected, 103)

        selected_all = get_random_proxy_id(exclude_id={101, 102, 103})
        self.assertIsNone(selected_all)


if __name__ == "__main__":
    unittest.main()
