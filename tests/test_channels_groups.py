"""Channels and groups management menu, adapted to encrypted sessions."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from babel.messages.pofile import read_po
from pyrogram.enums import ChatMemberStatus, ChatType
from pyrogram.errors import UserNotParticipant
from pyrogram.raw.functions.channels import GetLeftChannels
from pyrogram.raw.functions.messages import MigrateChat
from src.user_handlers import (
    _positive_basic_group_id,
    account_detail_menu,
    channels_and_groups_menu,
    manage_account_callback,
)

ROOT = Path(__file__).resolve().parents[1]

ARABIC = {
    "📢 Channels & Groups": "📢 القنوات والمجموعات",
    "Advanced Channel & Group Management": "إدارة متقدمة للقنوات والمجموعات",
    "View Left Channels": "عرض القنوات المغادرة",
    "Group Tools": "أدوات المجموعات",
    "Fetching group data...": "جاري جلب بيانات المجموعات...",
    "Upgrade Normal Groups": "ترقية المجموعات العادية",
    "An error occurred while fetching group data.": "حدث خطأ أثناء جلب بيانات المجموعة.",
    "Fetching left channels... Please wait.": "جاري جلب القنوات المغادرة... يرجى الانتظار.",
    "No left channels found.": "لم يتم العثور على قنوات مغادرة.",
    "<b>Left Channels:</b>\n\n": "<b>القنوات المغادرة:</b>\n\n",
    "An error occurred while fetching left chats.": "حدث خطأ أثناء جلب المحادثات المغادرة.",
    "Upgrading groups... This may take a moment.": (
        "جاري ترقية المجموعات... قد يستغرق هذا بعض الوقت."
    ),
    "All upgradable groups have been processed.": "تمت معالجة جميع المجموعات القابلة للترقية.",
    "An error occurred during the upgrade process.": "حدث خطأ أثناء عملية الترقية.",
}

STATS_ID = (
    "<b>Group Statistics</b>\n\n"
    "Total owned groups: {owned_count}\n"
    "Normal groups (can be upgraded): {normal_count}"
)
STATS_AR = (
    "<b>إحصائيات المجموعات</b>\n\n"
    "إجمالي المجموعات المملوكة: {owned_count}\n"
    "المجموعات العادية (يمكن ترقيتها): {normal_count}"
)


def _catalog(lang: str):
    path = ROOT / "locales" / lang / "LC_MESSAGES" / "base.po"
    with path.open("rb") as handle:
        return read_po(handle)


def test_channel_strings_are_in_both_catalogs():
    english = _catalog("en")
    arabic = _catalog("ar")
    for msgid, msgstr in {**ARABIC, STATS_ID: STATS_AR}.items():
        assert english.get(msgid) is not None
        assert english.get(msgid).string == msgid
        assert arabic.get(msgid) is not None
        assert arabic.get(msgid).string == msgstr


def test_positive_basic_group_id_flips_pyrogram_ids():
    assert _positive_basic_group_id(-42) == 42
    with pytest.raises(ValueError):
        _positive_basic_group_id(7)


class _Chat:
    def __init__(self, chat_id: int, title: str, chat_type: ChatType):
        self.id = chat_id
        self.title = title
        self.type = chat_type


class _Dialog:
    def __init__(self, chat: _Chat):
        self.chat = chat


class _Member:
    def __init__(self, status: ChatMemberStatus):
        self.status = status


class FakeClient:
    def __init__(self, dialogs: list[_Dialog], left_chats: list[_Chat] | None = None):
        self.dialogs = dialogs
        self.left_chats = left_chats or []
        self.member_errors: dict[int, Exception] = {}
        self.is_connected = False
        self.invoked: list[object] = []

    async def connect(self) -> None:
        self.is_connected = True

    async def disconnect(self) -> None:
        self.is_connected = False

    async def get_me(self):
        me = MagicMock()
        me.id = 99
        return me

    async def get_dialogs(self):
        for dialog in self.dialogs:
            yield dialog

    async def get_chat_member(self, chat_id: int, user: int):
        error = self.member_errors.get(chat_id)
        if error is not None:
            raise error
        return _Member(ChatMemberStatus.OWNER)

    async def invoke(self, request: object):
        self.invoked.append(request)
        result = MagicMock()
        result.chats = self.left_chats
        return result


def _callback(data: str, user_id: int = 42):
    update = MagicMock()
    update.effective_user.id = user_id
    query = MagicMock()
    query.data = data
    query.from_user.id = user_id
    query.message.message_id = 77
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()
    query.message.delete = AsyncMock()
    update.callback_query = query
    context = MagicMock()
    context.bot.edit_message_text = AsyncMock()
    return update, context, query


def _callback_data(markup) -> list[str]:
    found = []
    for row in markup.inline_keyboard:
        for button in row:
            if button is not None and button.callback_data:
                found.append(button.callback_data)
    return found


def _account() -> dict:
    return {
        "id": 5,
        "phone": "+1555",
        "code_monitor_enabled": False,
        "last_error": None,
        "is_active": False,
        "next_creation_time": None,
        "proxy_string": None,
        "proxy_id": None,
        "last_creation_time": None,
        "total_groups": 0,
        "session_status": "ok",
    }


def _device_profile() -> dict[str, object]:
    return {
        "api_id": 1,
        "api_hash": "hash",
        "device_model": "Pixel",
        "system_version": "14",
        "app_version": "10",
        "lang_code": "en",
    }


async def test_account_menu_includes_channels_and_groups():
    update, context, _query = _callback("mng_select_5")
    with (
        patch("src.user_handlers.get_translation_func_for_user", return_value=lambda text: text),
        patch("src.user_handlers.user_owns_account", return_value=True),
        patch("src.user_handlers.user_is_account_owner", return_value=True),
        patch("src.user_handlers.get_account_details", return_value=_account()),
        patch(
            "src.user_handlers.load_identity_for_menu", new_callable=AsyncMock, return_value=None
        ),
    ):
        await account_detail_menu(update, context, 5, 77)
    markup = context.bot.edit_message_text.await_args.kwargs["reply_markup"]
    assert "mng_channels_5" in _callback_data(markup)


async def test_channels_menu_offers_left_channels_and_group_tools():
    update, context, _query = _callback("mng_channels_5")
    with (
        patch("src.user_handlers.get_translation_func_for_user", return_value=lambda text: text),
        patch("src.user_handlers.user_owns_account", return_value=True),
    ):
        await channels_and_groups_menu(update, context, 5, 77)
    markup = context.bot.edit_message_text.await_args.kwargs["reply_markup"]
    assert _callback_data(markup) == [
        "mng_leftchannels_5_0",
        "mng_grouptools_5",
        "mng_select_5",
    ]


async def test_group_tools_uses_decrypted_session_and_counts_owned_groups():
    update, context, query = _callback("mng_grouptools_5")
    owned = _Dialog(_Chat(-10, "Basic", ChatType.GROUP))
    left = _Dialog(_Chat(-11, "Gone", ChatType.GROUP))
    channel = _Dialog(_Chat(-100, "News", ChatType.CHANNEL))
    client = FakeClient([owned, left, channel])
    client.member_errors[-11] = UserNotParticipant("USER_NOT_PARTICIPANT")
    built: list[dict] = []

    def _build(name, **kwargs):
        built.append({"name": name, **kwargs})
        return client

    with (
        patch("src.user_handlers.get_translation_func_for_user", return_value=lambda text: text),
        patch("src.user_handlers.user_owns_account", return_value=True),
        patch("src.user_handlers.get_account_session_string", return_value="plain-session"),
        patch(
            "src.user_handlers.get_device_profile_by_account_id",
            return_value=_device_profile(),
        ),
        patch("src.user_handlers.build_user_client", side_effect=_build),
    ):
        await manage_account_callback(update, context)

    assert built[0]["session_string"] == "plain-session"
    assert built[0]["name"] == "user_session_tools_5"
    assert built[0]["device_model"] == "Pixel"
    text = query.edit_message_text.await_args.args[0]
    assert "Total owned groups: 1" in text
    assert "Normal groups (can be upgraded): 1" in text
    markup = query.edit_message_text.await_args.kwargs["reply_markup"]
    assert "mng_upgradegroups_5" in _callback_data(markup)
    assert client.is_connected is False


async def test_left_channels_paginates_with_the_decrypted_client():
    update, context, query = _callback("mng_leftchannels_5_3")
    chats = [_Chat(1, "Alpha", ChatType.CHANNEL), _Chat(2, "Beta", ChatType.CHANNEL)]
    client = FakeClient([], left_chats=chats)

    def _build(name, **kwargs):
        assert kwargs["session_string"] == "plain-session"
        assert name == "user_session_reader_5"
        return client

    with (
        patch("src.user_handlers.get_translation_func_for_user", return_value=lambda text: text),
        patch("src.user_handlers.user_owns_account", return_value=True),
        patch("src.user_handlers.get_account_session_string", return_value="plain-session"),
        patch("src.user_handlers.get_device_profile_by_account_id", return_value=None),
        patch("src.user_handlers.build_user_client", side_effect=_build),
    ):
        await manage_account_callback(update, context)

    request = client.invoked[0]
    assert isinstance(request, GetLeftChannels)
    assert request.offset == 3
    text = query.edit_message_text.await_args.args[0]
    assert "Alpha" in text
    assert "Beta" in text
    markup = query.edit_message_text.await_args.kwargs["reply_markup"]
    assert _callback_data(markup) == [
        "mng_leftchannels_5_1",
        "mng_leftchannels_5_5",
        "mng_channels_5",
    ]


async def test_upgrade_normal_groups_migrates_owned_basic_groups():
    update, context, query = _callback("mng_upgradegroups_5")
    basic = _Dialog(_Chat(-15, "Old", ChatType.GROUP))
    super_group = _Dialog(_Chat(-100123, "Already", ChatType.SUPERGROUP))
    client = FakeClient([basic, super_group])
    sleeps: list[int] = []

    async def _sleep(seconds: int) -> None:
        sleeps.append(seconds)

    def _build(name, **kwargs):
        assert kwargs["session_string"] == "plain-session"
        return client

    with (
        patch("src.user_handlers.get_translation_func_for_user", return_value=lambda text: text),
        patch("src.user_handlers.user_owns_account", return_value=True),
        patch("src.user_handlers.get_account_session_string", return_value="plain-session"),
        patch("src.user_handlers.get_device_profile_by_account_id", return_value=None),
        patch("src.user_handlers.build_user_client", side_effect=_build),
        patch("src.user_handlers.asyncio.sleep", side_effect=_sleep),
    ):
        await manage_account_callback(update, context)

    migrations = [item for item in client.invoked if isinstance(item, MigrateChat)]
    assert len(migrations) == 1
    assert migrations[0].chat_id == 15
    assert sleeps == [1]
    texts = [call.args[0] for call in query.edit_message_text.await_args_list if call.args]
    assert "All upgradable groups have been processed." in texts
    assert any("Total owned groups: 2" in text for text in texts)


async def test_channel_actions_refuse_accounts_the_user_does_not_own():
    update, context, query = _callback("mng_leftchannels_5_0")
    with (
        patch("src.user_handlers.get_translation_func_for_user", return_value=lambda text: text),
        patch("src.user_handlers.user_owns_account", return_value=False),
        patch("src.user_handlers.build_user_client") as build,
    ):
        await manage_account_callback(update, context)
    build.assert_not_called()
    assert query.edit_message_text.await_args.args[0] == (
        "Error: Account not found or you don't have permission."
    )


async def test_group_tools_reports_a_missing_session():
    update, context, query = _callback("mng_grouptools_5")
    with (
        patch("src.user_handlers.get_translation_func_for_user", return_value=lambda text: text),
        patch("src.user_handlers.user_owns_account", return_value=True),
        patch("src.user_handlers.get_account_session_string", return_value=None),
        patch("src.user_handlers.build_user_client") as build,
    ):
        await manage_account_callback(update, context)
    build.assert_not_called()
    assert query.edit_message_text.await_args.args[0] == (
        "Error: Could not retrieve session for this account."
    )
