"""Phase 3 userbot plugins. Clients are mocks; nothing talks to Telegram."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from src.plugins.admin import plugin as admin_plugin
from src.plugins.afk import on_message as afk_message
from src.plugins.autoreply import on_message as autoreply_message
from src.plugins.autoreply import plugin as autoreply_plugin
from src.plugins.broadcast import plugin as broadcast_plugin
from src.plugins.broadcast import wants_chat
from src.plugins.common import lock_matches
from src.plugins.createchat import plugin as create_plugin
from src.plugins.games import on_message as games_message
from src.plugins.games import plugin as games_plugin
from src.plugins.gifts import format_gift_line
from src.plugins.gifts import plugin as gifts_plugin
from src.plugins.jobs import cancel_job
from src.plugins.locks import on_message as locks_message
from src.plugins.locks import plugin as locks_plugin
from src.plugins.pmpermit import on_message as permit_message
from src.plugins.pmpermit import plugin as permit_plugin
from src.plugins.storage import on_message as storage_message
from src.plugins.storage import plugin as storage_plugin
from src.plugins.tagall import plugin as tag_plugin
from src.runtime.flood import AccountLimiter
from src.runtime.plugins import AccountSession, Dispatcher, resolve_command
from src.runtime.settings_form import coerce_setting


class Owner:
    id = 111
    first_name = "Owner"
    username = "owner"


def _use_db(database, tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_FILE", tmp_path / "bot.db")


def _prepare(database, phone: str = "+15550001111") -> int:
    database.initialize_database()
    database.update_user_details(Owner())
    assert database.add_plan("Phase3", 10, 1.0, 30, 5, 10)
    plan_id = database.get_all_plans()[0]["id"]
    assert database.grant_subscription(111, plan_id, 30)[0]
    profile = database.get_random_device_profile()
    assert database.add_managed_account(111, phone, "session-string", profile["id"])
    return database.get_user_details(111)["accounts"][0]["id"]


def _message(text: str, *, chat_id: int = 50, chat_type: str = "supergroup", reply=None, **extra):
    data = dict(
        outgoing=True,
        text=text,
        id=20,
        chat=SimpleNamespace(id=chat_id, type=chat_type, title="Room"),
        from_user=SimpleNamespace(id=5, is_self=True, is_bot=False),
        reply_to_message=reply,
    )
    data.update(extra)
    return SimpleNamespace(**data)


class FakeClient:
    def __init__(self) -> None:
        self.me = SimpleNamespace(id=5)
        self.calls: list[tuple] = []
        self.sent: list[tuple] = []
        self.members: list[object] = []
        self.dialogs: list[object] = []
        self.gifts: list[object] = []
        self.balance = 40
        self.fail_send_ids: set[int] = set()

    def _call(self, name: str, args: tuple) -> None:
        self.calls.append((name, args))

    async def send_message(self, chat_id, text, **kwargs):
        self.sent.append((chat_id, text, kwargs))
        return SimpleNamespace(id=len(self.sent), chat=SimpleNamespace(id=chat_id), text=text)

    async def ban_chat_member(self, chat_id, user_id):
        self._call("ban", (chat_id, user_id))

    async def unban_chat_member(self, chat_id, user_id):
        self._call("unban", (chat_id, user_id))

    async def restrict_chat_member(self, chat_id, user_id, permissions):
        self._call("restrict", (chat_id, user_id, permissions.can_send_messages))

    async def promote_chat_member(self, chat_id, user_id, privileges):
        self._call("promote", (chat_id, user_id, privileges.can_delete_messages))

    async def pin_chat_message(self, chat_id, message_id):
        self._call("pin", (chat_id, message_id))

    async def unpin_chat_message(self, chat_id, message_id):
        self._call("unpin", (chat_id, message_id))

    async def delete_messages(self, chat_id, message_ids):
        self._call("delete", (chat_id, message_ids))

    async def forward_messages(self, chat_id, from_chat_id, message_ids):
        self._call("forward", (chat_id, from_chat_id, message_ids))

    async def block_user(self, user_id):
        self._call("block", (user_id,))

    async def create_supergroup(self, title, description=""):
        self._call("supergroup", (title, description))
        return SimpleNamespace(id=-1001, title=title)

    async def create_channel(self, title, description=""):
        self._call("channel", (title, description))
        return SimpleNamespace(id=-1002, title=title)

    async def transfer_chat_ownership(self, chat_id, user_id, password):
        self._call("transfer", (chat_id, user_id, password))
        return True

    async def get_available_gifts(self):
        return list(self.gifts)

    async def get_stars_balance(self):
        return self.balance

    async def send_gift(self, chat_id, gift_id, **kwargs):
        self._call("gift", (chat_id, gift_id))
        return SimpleNamespace(id=1)

    async def edit_message_text(self, chat_id, message_id, text):
        self._call("edit", (chat_id, message_id, text))

    async def get_chat_members(self, chat_id):
        for member in self.members:
            yield member

    async def get_dialogs(self):
        for dialog in self.dialogs:
            yield dialog


def _limiter(account_id: int) -> AccountLimiter:
    return AccountLimiter(account_id, min_interval=0, retry_threshold=0)


def _session(account_id: int, client: FakeClient) -> AccountSession:
    import asyncio

    return AccountSession(
        account_id=account_id,
        client=client,
        details={"language_code": "en"},
        limiter=_limiter(account_id),
        stop_event=asyncio.Event(),
        refresh=asyncio.Event(),
    )


async def _run(account_id: int, client: FakeClient, text: str, **message_kwargs) -> list[tuple]:
    dispatcher = Dispatcher()
    await dispatcher.handle_message(
        account_id=account_id,
        client=client,
        message=_message(text, **message_kwargs),
        limiter=_limiter(account_id),
        language="en",
    )
    return client.sent


def test_longest_command_wins():
    global_rule = resolve_command(".رد عام hello | hi", ".")
    local_rule = resolve_command(".رد hello | hi", ".")
    delete_global = resolve_command(".حذف رد عام hello", ".")
    assert global_rule is not None and global_rule[1].name == "رد عام"
    assert local_rule is not None and local_rule[1].name == "رد"
    assert delete_global is not None and delete_global[1].name == "حذف رد عام"
    assert global_rule[2] == "hello | hi"


def test_lock_matching_is_type_specific():
    photo = SimpleNamespace(photo=object(), text="", service=False)
    sticker = SimpleNamespace(sticker=object(), text="", service=False)
    forwarded = SimpleNamespace(text="hi", forward_from=object(), service=False)
    plain = SimpleNamespace(text="hello", service=False)
    assert lock_matches(photo, {"photos"})
    assert not lock_matches(photo, {"stickers"})
    assert lock_matches(sticker, {"stickers", "media"})
    assert lock_matches(forwarded, {"forwards"})
    assert lock_matches(plain, {"all"})
    assert not lock_matches(plain, {"links"})
    assert lock_matches(SimpleNamespace(text="see https://t.me/x", service=False), {"links"})


def test_broadcast_targets_skip_bots_and_channels():
    assert wants_chat(SimpleNamespace(type="group", id=1), "groups")
    assert not wants_chat(SimpleNamespace(type="channel", id=2), "groups")
    assert wants_chat(SimpleNamespace(type="private", id=3), "private")
    assert not wants_chat(SimpleNamespace(type="bot", id=4), "private")
    assert not wants_chat(SimpleNamespace(type="group", id=1), "private")


def test_setting_values_are_checked():
    field = next(item for item in permit_plugin.meta.settings if item.key == "warn_limit")
    assert coerce_setting(field, "4") == (True, 4)
    assert coerce_setting(field, "0")[0] is False
    assert coerce_setting(field, "-") == (True, 3)
    enabled = next(item for item in storage_plugin.meta.settings if item.key == "enabled")
    assert coerce_setting(enabled, "on") == (True, True)


def test_gift_price_line_marks_sold_out():
    gift = SimpleNamespace(id=9, price=15, title="Rose", is_sold_out=True, is_limited=False)
    assert "sold out" in format_gift_line(gift, "en")
    assert "نفد" in format_gift_line(gift, "ar")


async def test_admin_actions_use_reply_username_and_id(tmp_path, monkeypatch):
    import src.database as database

    _use_db(database, tmp_path, monkeypatch)
    account_id = _prepare(database)
    client = FakeClient()
    reply = SimpleNamespace(id=8, from_user=SimpleNamespace(id=77, is_self=False))
    await _run(account_id, client, ".ban", reply=reply)
    assert ("ban", (50, 77)) in client.calls

    await _run(account_id, client, ".unban @alice")
    assert ("unban", (50, "@alice")) in client.calls

    await _run(account_id, client, ".kick 88")
    assert ("ban", (50, 88)) in client.calls
    assert ("unban", (50, 88)) in client.calls

    await _run(account_id, client, ".mute", reply=reply)
    assert client.calls[-1][0] == "restrict"
    assert client.calls[-1][1][2] is False

    await _run(account_id, client, ".promote", reply=reply)
    assert client.calls[-1][0] == "promote"
    assert client.calls[-1][1][2] is True

    await _run(account_id, client, ".pin", reply=reply)
    assert ("pin", (50, 8)) in client.calls

    await _run(account_id, client, ".del", reply=reply)
    assert ("delete", (50, [8])) in client.calls

    await _run(account_id, client, ".purge", reply=reply)
    deleted = [call for call in client.calls if call[0] == "delete"][-1][1][1]
    assert deleted[0] == 8
    assert 20 not in deleted
    assert len(deleted) <= 100

    client.sent.clear()
    await _run(account_id, client, ".ban", chat_type="private")
    assert "Use this in a group" in client.sent[-1][1]
    assert admin_plugin.meta.commands


async def test_storage_logs_private_messages_and_mentions(tmp_path, monkeypatch):
    import src.database as database

    _use_db(database, tmp_path, monkeypatch)
    account_id = _prepare(database)
    client = FakeClient()
    session = _session(account_id, client)
    incoming = SimpleNamespace(
        outgoing=False,
        text="hello",
        id=3,
        chat=SimpleNamespace(id=90, type="private"),
        from_user=SimpleNamespace(id=90, is_self=False, is_bot=False),
        mentioned=False,
    )
    await storage_message(session, incoming)
    assert client.calls == []
    await _run(account_id, client, ".storage on", chat_type="private", chat_id=5)
    await storage_message(session, incoming)
    assert ("forward", ("me", 90, 3)) in client.calls
    mention = SimpleNamespace(
        outgoing=False,
        text="hey",
        id=4,
        chat=SimpleNamespace(id=50, type="supergroup"),
        from_user=SimpleNamespace(id=9, is_self=False),
        mentioned=True,
    )
    await storage_message(session, mention)
    assert ("forward", ("me", 50, 4)) in client.calls


async def test_auto_reply_prefers_the_current_chat(tmp_path, monkeypatch):
    import src.database as database

    _use_db(database, tmp_path, monkeypatch)
    account_id = _prepare(database)
    client = FakeClient()
    await _run(account_id, client, ".gfilter hello | global", chat_id=50)
    await _run(account_id, client, ".filter hello | local", chat_id=50)
    session = _session(account_id, client)
    incoming = SimpleNamespace(
        outgoing=False,
        text="say hello please",
        id=1,
        chat=SimpleNamespace(id=50, type="supergroup"),
        from_user=SimpleNamespace(id=9, is_self=False),
    )
    await autoreply_message(session, incoming)
    assert client.sent[-1][1] == "local"
    other = SimpleNamespace(
        outgoing=False,
        text="hello",
        id=2,
        chat=SimpleNamespace(id=70, type="private"),
        from_user=SimpleNamespace(id=9, is_self=False),
    )
    await autoreply_message(session, other)
    assert client.sent[-1][1] == "global"
    await _run(account_id, client, ".filters", chat_id=50)
    assert "hello" in client.sent[-1][1]
    assert autoreply_plugin.meta.name == "autoreply"


async def test_afk_replies_once_and_clears_on_outgoing(tmp_path, monkeypatch):
    import src.database as database

    _use_db(database, tmp_path, monkeypatch)
    account_id = _prepare(database)
    client = FakeClient()
    await _run(account_id, client, ".afk back soon", chat_type="private", chat_id=5)
    session = _session(account_id, client)
    incoming = SimpleNamespace(
        outgoing=False,
        text="you there?",
        id=1,
        chat=SimpleNamespace(id=90, type="private"),
        from_user=SimpleNamespace(id=90, is_self=False, is_bot=False),
    )
    await afk_message(session, incoming)
    await afk_message(session, incoming)
    replies = [item for item in client.sent if item[1] == "back soon"]
    assert len(replies) == 1
    outgoing = SimpleNamespace(
        outgoing=True,
        text="I am back",
        id=2,
        chat=SimpleNamespace(id=90, type="private"),
        from_user=SimpleNamespace(id=5, is_self=True),
    )
    await afk_message(session, outgoing)
    assert any("Away mode is off" in item[1] for item in client.sent)
    await afk_message(session, incoming)
    assert len([item for item in client.sent if item[1] == "back soon"]) == 1


async def test_pm_permit_warns_then_blocks(tmp_path, monkeypatch):
    import src.database as database

    _use_db(database, tmp_path, monkeypatch)
    account_id = _prepare(database)
    client = FakeClient()
    await _run(account_id, client, ".pmpermit on", chat_type="private", chat_id=5)
    await _run(account_id, client, ".pmwarn 2", chat_type="private", chat_id=5)
    session = _session(account_id, client)
    incoming = SimpleNamespace(
        outgoing=False,
        text="hi",
        id=1,
        chat=SimpleNamespace(id=90, type="private"),
        from_user=SimpleNamespace(id=90, is_self=False, is_bot=False),
    )
    await permit_message(session, incoming)
    assert client.calls == []
    await permit_message(session, incoming)
    assert ("block", (90,)) in client.calls
    await _run(account_id, client, ".approve 90", chat_type="private", chat_id=90)
    client.calls.clear()
    await permit_message(session, incoming)
    assert client.calls == []
    assert permit_plugin.meta.settings


async def test_locks_delete_matching_messages(tmp_path, monkeypatch):
    import src.database as database

    _use_db(database, tmp_path, monkeypatch)
    account_id = _prepare(database)
    client = FakeClient()
    await _run(account_id, client, ".lock stickers")
    session = _session(account_id, client)
    sticker = SimpleNamespace(
        outgoing=False,
        text="",
        id=11,
        sticker=object(),
        chat=SimpleNamespace(id=50, type="supergroup"),
        from_user=SimpleNamespace(id=9, is_self=False),
        service=False,
    )
    photo = SimpleNamespace(
        outgoing=False,
        text="",
        id=12,
        photo=object(),
        chat=SimpleNamespace(id=50, type="supergroup"),
        from_user=SimpleNamespace(id=9, is_self=False),
        service=False,
    )
    await locks_message(session, sticker)
    await locks_message(session, photo)
    assert ("delete", (50, [11])) in client.calls
    assert not any(call[1][1] == [12] for call in client.calls if call[0] == "delete")
    await _run(account_id, client, ".locks")
    assert "stickers" in client.sent[-1][1]
    assert locks_plugin.meta.name == "locks"


async def test_tagall_is_batched_and_cancellable(tmp_path, monkeypatch):
    import src.database as database

    _use_db(database, tmp_path, monkeypatch)
    account_id = _prepare(database)
    client = FakeClient()
    client.members = [
        SimpleNamespace(
            user=SimpleNamespace(
                id=index, first_name=f"U{index}", is_bot=False, is_deleted=False, is_self=False
            )
        )
        for index in range(1, 8)
    ]
    client.members.append(
        SimpleNamespace(user=SimpleNamespace(id=5, first_name="Me", is_bot=False, is_self=True))
    )
    client.members.append(
        SimpleNamespace(user=SimpleNamespace(id=99, first_name="Bot", is_bot=True, is_self=False))
    )

    async def send_message(chat_id, text, **kwargs):
        client.sent.append((chat_id, text, kwargs))
        if kwargs.get("parse_mode") == "html":
            cancel_job(account_id, "tagall")
        return SimpleNamespace(id=1, chat=SimpleNamespace(id=chat_id), text=text)

    client.send_message = send_message  # type: ignore[method-assign]
    from src.runtime.store import set_plugin_setting

    set_plugin_setting(account_id, "tagall", "batch", 3)
    set_plugin_setting(account_id, "tagall", "max_members", 20)
    await _run(account_id, client, ".tagall hello")
    html_sends = [item for item in client.sent if item[2].get("parse_mode") == "html"]
    assert len(html_sends) == 1
    assert "tg://user?id=1" in html_sends[0][1]
    assert "Stopped" in client.sent[-1][1]
    assert tag_plugin.meta.commands


async def test_broadcast_confirms_reports_and_cancels(tmp_path, monkeypatch):
    import src.database as database

    _use_db(database, tmp_path, monkeypatch)
    account_id = _prepare(database)
    client = FakeClient()
    client.dialogs = [
        SimpleNamespace(chat=SimpleNamespace(id=1, type="group")),
        SimpleNamespace(chat=SimpleNamespace(id=2, type="channel")),
        SimpleNamespace(chat=SimpleNamespace(id=3, type="supergroup")),
        SimpleNamespace(chat=SimpleNamespace(id=4, type="bot")),
    ]
    await _run(account_id, client, ".broadcast hello groups")
    assert not any(item[0] in {1, 3} and item[1] == "hello groups" for item in client.sent)
    assert "confirmbroadcast" in client.sent[-1][1]

    async def send_message(chat_id, text, **kwargs):
        client.sent.append((chat_id, text, kwargs))
        if text == "hello groups":
            cancel_job(account_id, "broadcast")
        return SimpleNamespace(id=len(client.sent), chat=SimpleNamespace(id=chat_id), text=text)

    client.send_message = send_message  # type: ignore[method-assign]
    await _run(account_id, client, ".confirmbroadcast")
    delivered = [item[0] for item in client.sent if item[1] == "hello groups"]
    assert delivered == [1]
    assert "cancelled: True" in client.sent[-1][1]
    assert broadcast_plugin.meta.settings


async def test_create_and_transfer_does_not_keep_the_password(tmp_path, monkeypatch):
    import src.database as database

    _use_db(database, tmp_path, monkeypatch)
    account_id = _prepare(database)
    client = FakeClient()
    await _run(account_id, client, ".creategroup Weekend plans")
    assert ("supergroup", ("Weekend plans", "")) in client.calls
    await _run(account_id, client, ".create channel News")
    assert ("channel", ("News", "")) in client.calls
    reply = SimpleNamespace(id=4, from_user=SimpleNamespace(id=77))
    await _run(account_id, client, ".transfer", reply=reply)
    assert "cloudpass" in client.sent[-1][1]
    await _run(
        account_id,
        client,
        ".cloudpass hunter2",
        chat_id=5,
        chat_type="private",
    )
    assert ("delete", (5, 20)) in client.calls
    assert ("transfer", (50, 77, "hunter2")) in client.calls
    assert all("hunter2" not in item[1] for item in client.sent)
    assert create_plugin.meta.name == "create"


async def test_gifts_list_and_require_confirmation(tmp_path, monkeypatch):
    import src.database as database

    _use_db(database, tmp_path, monkeypatch)
    account_id = _prepare(database)
    client = FakeClient()
    client.gifts = [
        SimpleNamespace(
            id=15, price=25, title="Rose", is_sold_out=False, is_limited=True, available_amount=3
        ),
        SimpleNamespace(
            id=16, price=50, title="Gone", is_sold_out=True, is_limited=False, available_amount=0
        ),
    ]
    await _run(account_id, client, ".giftprices", chat_type="private", chat_id=5)
    assert "15" in client.sent[-1][1]
    assert "balance 40" in client.sent[-1][1]
    reply = SimpleNamespace(id=2, from_user=SimpleNamespace(id=77))
    await _run(account_id, client, ".gift 15", reply=reply, chat_type="private", chat_id=77)
    assert not any(call[0] == "gift" for call in client.calls)
    assert "giftconfirm" in client.sent[-1][1]
    await _run(account_id, client, ".giftconfirm", chat_type="private", chat_id=5)
    assert ("gift", (77, 15)) in client.calls
    await _run(account_id, client, ".gift 16", reply=reply, chat_type="private", chat_id=77)
    assert "sold out" in client.sent[-1][1]
    assert gifts_plugin.meta.commands


async def test_games_only_notify_saved_messages(tmp_path, monkeypatch):
    import src.database as database

    _use_db(database, tmp_path, monkeypatch)
    account_id = _prepare(database)
    client = FakeClient()
    await _run(account_id, client, ".gamewatch")
    session = _session(account_id, client)
    prompt = SimpleNamespace(
        outgoing=False,
        text="اسرع: بحر",
        id=9,
        chat=SimpleNamespace(id=50, type="supergroup", title="Games"),
        from_user=SimpleNamespace(id=800, is_self=False, is_bot=True),
    )
    await games_message(session, prompt)
    assert client.sent[-1][0] == "me"
    assert "اسرع" in client.sent[-1][1]
    assert all(item[0] != 50 or "اسرع" not in item[1] for item in client.sent)
    await games_message(session, prompt)
    notices = [item for item in client.sent if item[0] == "me" and "اسرع" in item[1]]
    assert len(notices) == 1
    assert games_plugin.meta.description_en.startswith("Tell Saved Messages")


def test_plugins_are_registered_for_new_plans(tmp_path, monkeypatch):
    import src.database as database
    from src.runtime.gating import list_plugin_views

    _use_db(database, tmp_path, monkeypatch)
    account_id = _prepare(database)
    views = list_plugin_views(account_id, "ar")
    assert views is not None
    names = {view["name"] for view in views}
    for name in (
        "admin",
        "storage",
        "autoreply",
        "afk",
        "pmpermit",
        "locks",
        "tagall",
        "broadcast",
        "create",
        "gifts",
        "games",
    ):
        assert name in names
    admin = next(view for view in views if view["name"] == "admin")
    assert admin["allowed"] is True
    assert "حظر" in admin["commands"]


@pytest.mark.parametrize(
    ("plugin_name", "arabic"),
    [
        ("admin", "حظر"),
        ("storage", "تخزين"),
        ("afk", "غائب"),
        ("pmpermit", "الحماية"),
        ("locks", "قفل"),
        ("tagall", "تاك"),
        ("broadcast", "اذاعة"),
        ("create", "انشاء كروب"),
        ("gifts", "اسعار الهدايا"),
        ("games", "تفعيل اللعبة"),
    ],
)
def test_each_plugin_has_arabic_and_english_commands(plugin_name, arabic):
    from src.runtime.plugins import get_plugin

    plugin = get_plugin(plugin_name)
    assert plugin is not None
    names = [command.name for command in plugin.meta.commands]
    assert arabic in names
    assert any(command.name.isascii() for command in plugin.meta.commands)
    assert plugin.meta.description_ar
    assert plugin.meta.description_en
