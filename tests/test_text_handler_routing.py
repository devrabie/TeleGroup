"""Regression tests: free-text replies must reach the right handler.

PTB runs only the first matching handler in a group. A catch-all text
handler registered before ``add_account_conv_handler`` used to swallow the
phone number, login code and 2FA password, and also shadowed the plugin
setting input. These tests push real ``Update`` objects through a real
``Application`` with the production handler list.
"""

from __future__ import annotations

import itertools
from collections.abc import Iterator
from contextlib import ExitStack
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pyrogram.errors import SessionPasswordNeeded
from src.admin_handlers import admin_handlers_list
from src.user_handlers import CODE, PASSWORD, PHONE, add_account_conv_handler, user_handlers_list
from telegram import Update
from telegram.ext import (
    Application,
    BaseHandler,
    ConversationHandler,
    ExtBot,
    MessageHandler,
)

USER_ID = 424242
PHONE_NUMBER = "+1234567890"
_ids = itertools.count(1)


def _identity_translation(_user_id: int) -> Any:
    return lambda text: text


def _user_json() -> dict[str, Any]:
    return {"id": USER_ID, "is_bot": False, "first_name": "Tester"}


def _message_json(text: str) -> dict[str, Any]:
    return {
        "message_id": next(_ids),
        "date": int(datetime.now(UTC).timestamp()),
        "chat": {"id": USER_ID, "type": "private"},
        "from": _user_json(),
        "text": text,
    }


def _text_update(app: Application, text: str) -> Update:
    data: dict[str, Any] = {"update_id": next(_ids), "message": _message_json(text)}
    update = Update.de_json(data, app.bot)
    assert update is not None
    return update


def _callback_update(app: Application, data: str) -> Update:
    message = _message_json("menu")
    message["from"] = {"id": 1, "is_bot": True, "first_name": "Bot"}
    payload = {
        "update_id": next(_ids),
        "callback_query": {
            "id": str(next(_ids)),
            "from": _user_json(),
            "chat_instance": "ci",
            "data": data,
            "message": message,
        },
    }
    update = Update.de_json(payload, app.bot)
    assert update is not None
    return update


def _conversation_state(app: Application) -> object:
    del app
    # per_chat + per_user, not per_message: key is (chat_id, user_id).
    return add_account_conv_handler._conversations.get((USER_ID, USER_ID))


@pytest.fixture
def router_targets() -> Iterator[tuple[AsyncMock, AsyncMock]]:
    with (
        patch("src.text_router.receive_account_admin_text", new_callable=AsyncMock) as admin,
        patch("src.text_router.receive_plugin_setting", new_callable=AsyncMock) as plugin,
    ):
        yield admin, plugin


@pytest.fixture
async def app() -> Any:
    status_message = MagicMock()
    status_message.edit_text = AsyncMock()
    with ExitStack() as stack:
        # No network: the bot is never really initialized and API calls are stubbed.
        stack.enter_context(patch.object(ExtBot, "initialize", AsyncMock()))
        stack.enter_context(patch.object(ExtBot, "shutdown", AsyncMock()))
        stack.enter_context(
            patch.object(ExtBot, "send_message", AsyncMock(return_value=status_message))
        )
        stack.enter_context(patch.object(ExtBot, "edit_message_text", AsyncMock()))
        stack.enter_context(patch.object(ExtBot, "answer_callback_query", AsyncMock()))
        for module in ("src.user_handlers", "src.account_admin_menu", "src.plugin_menu"):
            stack.enter_context(
                patch(f"{module}.get_translation_func_for_user", _identity_translation)
            )
        application = Application.builder().token("1:test").build()
        application.add_handlers(admin_handlers_list + user_handlers_list)
        await application.initialize()
        add_account_conv_handler._conversations.clear()
        try:
            yield application
        finally:
            add_account_conv_handler._conversations.clear()
            await application.shutdown()


def _accepts_plain_text(handler: BaseHandler[Any, Any, Any], update: Update) -> bool:
    return isinstance(handler, MessageHandler) and bool(handler.check_update(update))


async def test_no_catch_all_text_handler_shadows_a_conversation(app: Application) -> None:
    handlers = admin_handlers_list + user_handlers_list
    for sender in (USER_ID, 1):  # a normal user and an admin from ADMIN_IDS
        data = {"update_id": next(_ids), "message": _message_json("hello")}
        data["message"]["from"]["id"] = sender
        update = Update.de_json(data, app.bot)
        assert update is not None
        catch_alls = [i for i, h in enumerate(handlers) if _accepts_plain_text(h, update)]
        conversations = [i for i, h in enumerate(handlers) if isinstance(h, ConversationHandler)]
        assert conversations
        assert len(catch_alls) <= 1
        if catch_alls:
            assert max(conversations) < min(catch_alls)


async def test_add_account_phone_code_and_password_reach_conversation(
    app: Application, router_targets: tuple[AsyncMock, AsyncMock]
) -> None:
    admin_target, plugin_target = router_targets
    client = MagicMock()
    client.is_connected = True
    client.sign_in = AsyncMock(side_effect=SessionPasswordNeeded())
    client.check_password = AsyncMock()

    async def fake_send_login_code(phone: str, context: Any, user_id: int, _: Any) -> tuple:
        context.user_data["pyrogram_client"] = client
        context.user_data["phone_code_hash"] = "hash"
        return True, None, None

    send_login_code = AsyncMock(side_effect=fake_send_login_code)
    complete_login = AsyncMock()
    with (
        patch(
            "src.user_handlers.get_user_details",
            return_value={"subscription": {"plan_id": 1}, "accounts": []},
        ),
        patch(
            "src.user_handlers.get_plan_by_id",
            return_value={"max_accounts": 5, "name": "Test"},
        ),
        patch("src.user_handlers.send_login_code", send_login_code),
        patch("src.user_handlers.async_complete_login", complete_login),
    ):
        await app.process_update(_callback_update(app, "start_add_account"))
        await app.process_update(_callback_update(app, "add_for_self"))
        assert _conversation_state(app) == PHONE

        await app.process_update(_text_update(app, PHONE_NUMBER))
        send_login_code.assert_awaited_once()
        assert send_login_code.await_args.args[0] == PHONE_NUMBER
        assert _conversation_state(app) == CODE

        await app.process_update(_text_update(app, "12345"))
        client.sign_in.assert_awaited_once_with(PHONE_NUMBER, "hash", "12345")
        assert _conversation_state(app) == PASSWORD

        await app.process_update(_text_update(app, "s3cret"))
        client.check_password.assert_awaited_once_with("s3cret")
        complete_login.assert_awaited_once()
        assert _conversation_state(app) is None

    admin_target.assert_not_awaited()
    plugin_target.assert_not_awaited()


async def test_pending_plugin_setting_text_reaches_plugin_handler(
    app: Application, router_targets: tuple[AsyncMock, AsyncMock]
) -> None:
    admin_target, plugin_target = router_targets
    app.user_data[USER_ID]["plugin_setting_field"] = {
        "account_id": 7,
        "plugin_name": "demo",
        "field_key": "k",
    }
    await app.process_update(_text_update(app, "value"))
    plugin_target.assert_awaited_once()
    admin_target.assert_not_awaited()


async def test_pending_admin_add_text_reaches_admin_handler(
    app: Application, router_targets: tuple[AsyncMock, AsyncMock]
) -> None:
    admin_target, plugin_target = router_targets
    app.user_data[USER_ID]["account_admin_add"] = 7
    await app.process_update(_text_update(app, "@someone"))
    admin_target.assert_awaited_once()
    plugin_target.assert_not_awaited()


async def test_admin_add_takes_priority_over_plugin_setting(
    app: Application, router_targets: tuple[AsyncMock, AsyncMock]
) -> None:
    admin_target, plugin_target = router_targets
    app.user_data[USER_ID]["account_admin_add"] = 7
    app.user_data[USER_ID]["plugin_setting_field"] = {"account_id": 7}
    await app.process_update(_text_update(app, "123"))
    admin_target.assert_awaited_once()
    plugin_target.assert_not_awaited()


async def test_text_without_pending_input_is_ignored(
    app: Application, router_targets: tuple[AsyncMock, AsyncMock]
) -> None:
    admin_target, plugin_target = router_targets
    await app.process_update(_text_update(app, "hello"))
    admin_target.assert_not_awaited()
    plugin_target.assert_not_awaited()


async def test_real_plugin_setting_handler_runs_for_pending_input(app: Application) -> None:
    user_data = app.user_data[USER_ID]
    user_data["plugin_setting_field"] = {"account_id": 7, "plugin_name": "demo", "field_key": "k"}
    await app.process_update(_text_update(app, "cancel"))
    assert "plugin_setting_field" not in user_data
    send = ExtBot.send_message
    assert isinstance(send, AsyncMock)
    assert send.await_args.kwargs["text"] == "Cancelled."


async def test_real_admin_add_handler_runs_for_pending_input(app: Application) -> None:
    user_data = app.user_data[USER_ID]
    user_data["account_admin_add"] = 7
    user_data["plugin_setting_field"] = {"account_id": 7, "plugin_name": "demo", "field_key": "k"}
    await app.process_update(_text_update(app, "cancel"))
    assert "account_admin_add" not in user_data
    # The plugin input is untouched: only one pending input is handled per message.
    assert "plugin_setting_field" in user_data
    send = ExtBot.send_message
    assert isinstance(send, AsyncMock)
    assert send.await_args.kwargs["text"] == "Cancelled."
