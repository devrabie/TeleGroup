"""Phase 2: supervisor, command dispatch, plan gating, and FloodWait."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from src.plugins.groups import plugin as groups_plugin
from src.plugins.ping import plugin as ping_plugin
from src.runtime.connect import SessionInvalid
from src.runtime.flood import AccountLimiter, ActionQueueFull, FloodDeferred
from src.runtime.notifier import RecordingNotifier
from src.runtime.plugins import (
    BotCommand,
    CommandContext,
    Dispatcher,
    Plugin,
    PluginMeta,
    PluginSettings,
)
from src.runtime.store import (
    account_is_running,
    ack_session_leases,
    lease_is_acked,
    set_plugin_setting,
)
from src.runtime.supervisor import Supervisor


class Owner:
    id = 111
    first_name = "Owner"
    username = "owner"


class FakeClient:
    def __init__(self) -> None:
        self.is_connected = False
        self.stopped = False
        self.handlers: list[object] = []
        self.me = SimpleNamespace(id=42)

    async def start(self) -> None:
        self.is_connected = True

    async def stop(self) -> None:
        self.is_connected = False
        self.stopped = True

    def add_handler(self, handler: object) -> None:
        self.handlers.append(handler)

    def remove_handler(self, handler: object) -> None:
        if handler in self.handlers:
            self.handlers.remove(handler)


class Sender:
    def __init__(self) -> None:
        self.sent: list[str] = []
        self.me = SimpleNamespace(id=5)

    async def send_message(self, chat_id: int, text: str, **kwargs: object) -> str:
        self.sent.append(text)
        return text


def _message(
    text: str, *, outgoing: bool = True, language_date: datetime | None = None
) -> SimpleNamespace:
    return SimpleNamespace(
        outgoing=outgoing,
        text=text,
        date=language_date if language_date is not None else datetime.now(UTC),
        id=7,
        chat=SimpleNamespace(id=99),
        from_user=SimpleNamespace(id=5, is_self=outgoing),
    )


class Boom(Plugin):
    meta = PluginMeta(
        name="boom",
        description_en="Explodes.",
        description_ar="ينفجر.",
        commands=(BotCommand("boom", "Explodes.", "ينفجر."),),
        default_enabled=True,
    )

    async def handle(self, ctx: CommandContext) -> None:
        raise RuntimeError("plugin failed")


def _prepare(database, phone: str = "+15550001111") -> tuple[int, int]:
    database.initialize_database()
    database.update_user_details(Owner())
    assert database.add_plan("Runtime", 10, 1.0, 30, 5, 10)
    plan_id = database.get_all_plans()[0]["id"]
    assert database.grant_subscription(111, plan_id, 30)[0]
    profile = database.get_random_device_profile()
    assert database.add_managed_account(111, phone, "session-string", profile["id"])
    account_id = database.get_user_details(111)["accounts"][0]["id"]
    return account_id, plan_id


def _use_db(database, tmp_path, monkeypatch):
    db_path = tmp_path / "bot.db"
    monkeypatch.setattr(database, "DB_FILE", db_path)
    return db_path


async def _wait_for(predicate, timeout: float = 2.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.02)
    raise AssertionError("condition was not met in time")


def test_settings_round_trip_is_per_plugin(tmp_path, monkeypatch):
    import src.database as database

    _use_db(database, tmp_path, monkeypatch)
    account_id, _plan_id = _prepare(database)
    settings = PluginSettings(account_id, "ping")
    settings.set("last", {"ms": 12})
    assert settings.get("last") == {"ms": 12}
    assert settings.get("missing", "fallback") == "fallback"
    settings.delete("last")
    assert settings.get("last") is None
    set_plugin_setting(account_id, "core", "prefix", "!")
    from src.runtime.plugins import command_prefix

    assert command_prefix(account_id) == "!"


def test_gating_toggles_and_plan_lock(tmp_path, monkeypatch):
    import src.database as database
    from src.runtime.gating import (
        list_plan_plugin_views,
        list_plugin_views,
        plugin_is_enabled,
        set_account_plugin,
    )
    from src.runtime.store import set_plan_plugin_allowed

    _use_db(database, tmp_path, monkeypatch)
    account_id, plan_id = _prepare(database)
    views = list_plugin_views(account_id, "en")
    assert views is not None
    by_name = {view["name"]: view for view in views}
    assert by_name["ping"]["enabled"] is True
    assert by_name["groups"]["enabled"] is False
    assert by_name["codemon"]["enabled"] is False
    assert by_name["ping"]["description"]

    assert set_account_plugin(account_id, 111, "ping") == "disabled"
    assert plugin_is_enabled(account_id, ping_plugin) is False
    assert set_account_plugin(account_id, 111, "ping") == "enabled"
    assert set_account_plugin(account_id, 999, "ping") == "denied"

    assert set_account_plugin(account_id, 111, "groups") == "enabled"
    assert database.get_account_details(account_id)["is_active"] == 1
    assert set_account_plugin(account_id, 111, "codemon") == "enabled"
    assert database.get_account_details(account_id)["code_monitor_enabled"] == 1

    assert set_plan_plugin_allowed(plan_id, "ping", False)
    assert set_account_plugin(account_id, 111, "ping") == "locked"
    plan_views = list_plan_plugin_views(plan_id, "ar")
    assert plan_views is not None
    ping_row = next(view for view in plan_views if view["name"] == "ping")
    assert ping_row["allowed"] is False
    assert "استجابة" in ping_row["description"]


async def test_dispatcher_prefix_arabic_owner_and_isolation(tmp_path, monkeypatch):
    import src.database as database
    from src.runtime.store import set_plan_plugin_allowed

    _use_db(database, tmp_path, monkeypatch)
    account_id, plan_id = _prepare(database)
    limiter = AccountLimiter(account_id, min_interval=0, retry_threshold=0)
    sender = Sender()
    dispatcher = Dispatcher()

    assert (
        await dispatcher.handle_message(
            account_id=account_id,
            client=sender,
            message=_message(".ping", outgoing=False),
            limiter=limiter,
        )
        is False
    )
    assert sender.sent == []

    assert await dispatcher.handle_message(
        account_id=account_id,
        client=sender,
        message=_message(".فحص"),
        limiter=limiter,
        language="ar",
    )
    assert sender.sent[-1].endswith("مللي ثانية")

    assert await dispatcher.handle_message(
        account_id=account_id,
        client=sender,
        message=_message(".id"),
        limiter=limiter,
    )
    assert "User:" in sender.sent[-1]
    assert "`5`" in sender.sent[-1]
    assert "`99`" in sender.sent[-1]

    database.set_user_language(111, "ar")
    assert await dispatcher.handle_message(
        account_id=account_id,
        client=sender,
        message=_message(".الاوامر"),
        limiter=limiter,
        language="ar",
    )
    assert "فحص" in sender.sent[-1]
    assert "ping" in sender.sent[-1]

    from src.runtime.gating import set_account_plugin

    assert set_account_plugin(account_id, 111, "ping") == "disabled"
    before = len(sender.sent)
    assert (
        await dispatcher.handle_message(
            account_id=account_id,
            client=sender,
            message=_message(".ping"),
            limiter=limiter,
        )
        is False
    )
    assert len(sender.sent) == before

    set_plugin_setting(account_id, "core", "prefix", "!")
    assert (
        await dispatcher.handle_message(
            account_id=account_id,
            client=sender,
            message=_message(".id"),
            limiter=limiter,
        )
        is False
    )
    assert await dispatcher.handle_message(
        account_id=account_id,
        client=sender,
        message=_message("!id"),
        limiter=limiter,
    )

    assert set_plan_plugin_allowed(plan_id, "boom", True)
    assert set_account_plugin(account_id, 111, "ping") == "enabled"
    boom = Boom()
    isolated = Dispatcher(plugins=[boom, ping_plugin])
    assert await isolated.handle_message(
        account_id=account_id,
        client=sender,
        message=_message("!boom"),
        limiter=limiter,
    )
    assert await isolated.handle_message(
        account_id=account_id,
        client=sender,
        message=_message("!ping"),
        limiter=limiter,
    )
    assert sender.sent[-1].endswith("ms") or sender.sent[-1].endswith("مللي ثانية")


def test_floodwait_retries_paces_and_fills_the_queue():
    clock = {"now": 0.0}
    sleeps: list[float] = []

    def monotonic() -> float:
        return clock["now"]

    async def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        clock["now"] += seconds

    seen: list[int] = []
    limiter = AccountLimiter(
        7,
        min_interval=0.5,
        max_queue=1,
        retry_threshold=5,
        clock=monotonic,
        sleeper=sleep,
        on_flood=seen.append,
    )

    class FloodWait(Exception):
        def __init__(self, value: int) -> None:
            self.value = value

    calls = {"n": 0}

    async def flaky() -> str:
        calls["n"] += 1
        if calls["n"] == 1:
            raise FloodWait(2)
        return "ok"

    async def run() -> None:
        assert await limiter.run(flaky) == "ok"
        assert seen == [2]
        assert 2 in sleeps

        async def quick() -> str:
            return "next"

        assert await limiter.run(quick) == "next"
        assert sleeps[-1] == pytest.approx(0.5)

        started = asyncio.Event()
        release = asyncio.Event()

        async def hold() -> str:
            started.set()
            await release.wait()
            return "held"

        # A fresh limiter so the pace sleep does not hide the queue check.
        queued = AccountLimiter(8, min_interval=0, max_queue=1, clock=monotonic, sleeper=sleep)
        first = asyncio.create_task(queued.run(hold))
        await started.wait()
        with pytest.raises(ActionQueueFull):
            await queued.run(quick)
        release.set()
        assert await first == "held"

        async def long_flood() -> None:
            raise FloodWait(40)

        deferring = AccountLimiter(
            9,
            min_interval=0,
            retry_threshold=5,
            clock=monotonic,
            sleeper=sleep,
        )
        with pytest.raises(FloodDeferred) as caught:
            await deferring.run(long_flood)
        assert caught.value.seconds == 40

    asyncio.run(run())


async def test_group_plugin_uses_the_existing_create_path():
    session = SimpleNamespace(
        account_id=4,
        client=object(),
        limiter=object(),
        stopped=lambda: False,
        sleep=AsyncMock(),
    )
    create = AsyncMock()
    with (
        patch("src.runtime.gating.plugin_is_enabled", return_value=True),
        patch(
            "src.database.get_eligible_accounts",
            return_value=[{"account_id": 4, "daily_group_limit": 1}],
        ),
        patch("src.automation.create_group_using_client", create),
    ):
        await groups_plugin.tick(session)
    create.assert_awaited()
    session.sleep.assert_awaited()

    create.reset_mock()
    with patch("src.runtime.gating.plugin_is_enabled", return_value=False):
        await groups_plugin.tick(session)
    create.assert_not_awaited()


async def test_supervisor_starts_stops_and_isolates_shards(tmp_path, monkeypatch):
    import src.database as database

    _use_db(database, tmp_path, monkeypatch)
    first, _plan_id = _prepare(database, "+15550001001")
    profile = database.get_random_device_profile()
    assert database.add_managed_account(111, "+15550001002", "session-two", profile["id"])
    second = database.get_user_details(111)["accounts"][1]["id"]
    clients: dict[int, FakeClient] = {}

    async def connect(details: dict) -> FakeClient:
        client = FakeClient()
        await client.start()
        clients[int(details["account_id"])] = client
        return client

    even_id = first if first % 2 == 0 else second
    odd_id = second if even_id == first else first
    supervisor = Supervisor(
        notifier=RecordingNotifier(),
        connector=connect,
        shard_id=even_id % 2,
        shard_count=2,
        poll_seconds=0.05,
        health_interval=30,
        listen=False,
        plugins=[],
    )
    await supervisor.start()
    try:
        await _wait_for(lambda: even_id in clients)
        assert odd_id not in clients
        assert account_is_running(even_id)
    finally:
        await supervisor.stop()
    assert clients[even_id].stopped
    assert account_is_running(even_id) is False


async def test_supervisor_marks_invalid_sessions_and_reconnects(tmp_path, monkeypatch):
    import src.database as database

    _use_db(database, tmp_path, monkeypatch)
    account_id, _plan_id = _prepare(database, "+15550001009")
    notifier = RecordingNotifier()
    calls = {"n": 0}

    async def connect(details: dict) -> FakeClient:
        calls["n"] += 1
        raise SessionInvalid("session revoked")

    supervisor = Supervisor(
        notifier=notifier,
        connector=connect,
        poll_seconds=0.05,
        health_interval=30,
        listen=False,
        plugins=[],
    )
    await supervisor.start()
    try:
        await _wait_for(lambda: len(notifier.messages) == 1)
        assert calls["n"] == 1
        account = database.get_account_details(account_id)
        assert account is not None
        assert account["session_status"] == "invalid"
        assert "revoked" in notifier.messages[0][1] or "ملغ" in notifier.messages[0][1]
        await asyncio.sleep(0.1)
        assert calls["n"] == 1
    finally:
        await supervisor.stop()

    calls["n"] = 0
    holder: dict[str, FakeClient] = {}

    async def flaky(details: dict) -> FakeClient:
        calls["n"] += 1
        if calls["n"] == 1:
            raise ConnectionError("proxy down")
        client = FakeClient()
        await client.start()
        holder["client"] = client
        return client

    # A new account id would be cleaner, but this one is invalid. Use another phone.
    profile = database.get_random_device_profile()
    assert database.add_managed_account(111, "+15550001010", "session-retry", profile["id"])
    retry_supervisor = Supervisor(
        notifier=RecordingNotifier(),
        connector=flaky,
        poll_seconds=0.05,
        health_interval=30,
        backoff_initial=0.01,
        backoff_max=0.05,
        listen=False,
        plugins=[],
    )
    await retry_supervisor.start()
    try:
        await _wait_for(lambda: calls["n"] >= 2)
        assert holder["client"].is_connected
    finally:
        await retry_supervisor.stop()
    assert holder["client"].stopped


async def test_remote_lease_waits_until_the_worker_acks(tmp_path, monkeypatch):
    import src.database as database
    from src.runtime.lease import pause_remote_runtime, resume_remote_runtime

    _use_db(database, tmp_path, monkeypatch)
    account_id, _plan_id = _prepare(database, "+15550001020")

    async def _pause() -> bool:
        return await pause_remote_runtime(
            account_id,
            role="bot",
            timeout=2,
            poll_seconds=0.05,
        )

    task = asyncio.create_task(_pause())
    await asyncio.sleep(0.1)
    assert lease_is_acked(account_id) is False
    ack_session_leases([account_id])
    assert await task is True
    assert lease_is_acked(account_id)
    await resume_remote_runtime(account_id, paused=True)
    assert lease_is_acked(account_id) is False
