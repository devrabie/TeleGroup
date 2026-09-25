"""Phase 4 plugins. Network, yt-dlp, and Telegram are mocked."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine, text
from src.config import Settings
from src.db.models import Base
from src.media_jobs import (
    classify_target,
    convert,
    first_entry,
    ocr,
    public_http_url,
    to_webp,
    ytdlp_options,
)
from src.plan_grants import PHASE4_PLUGIN_NAMES, grant_named_plugins, revoke_named_plugins
from src.plugins.calc import CalcError, safe_calc
from src.plugins.calc import plugin as calc_plugin
from src.plugins.clock import format_clock
from src.plugins.clock import plugin as clock_plugin
from src.plugins.convert import plugin as convert_plugin
from src.plugins.download import plugin as download_plugin
from src.plugins.info import format_chat_info
from src.plugins.info import plugin as info_plugin
from src.plugins.leave import can_leave
from src.plugins.leave import plugin as leave_plugin
from src.plugins.ocr import plugin as ocr_plugin
from src.plugins.profile import plugin as profile_plugin
from src.plugins.profile import split_name
from src.plugins.proxy_url import format_socks_url
from src.plugins.repeat import parse_repeat
from src.plugins.repeat import plugin as repeat_plugin
from src.plugins.stickers import format_pack, pack_is_full, pack_short_name, sticker_emoji
from src.plugins.stickers import plugin as stickers_plugin
from src.plugins.telegraph import plugin as telegraph_plugin
from src.plugins.translate import plugin as translate_plugin
from src.plugins.translate import split_translate_args
from src.plugins.tts import plugin as tts_plugin
from src.plugins.tts import split_tts
from src.runtime.builtin_plugins import DEFAULT_PLAN_PLUGINS
from src.runtime.flood import AccountLimiter
from src.runtime.plugins import CommandContext, resolve_command
from src.runtime.work_pool import run_named


class FakeClient:
    def __init__(self) -> None:
        self.me = SimpleNamespace(id=5)
        self.sent: list[tuple] = []
        self.calls: list[tuple] = []

    async def send_message(self, chat_id, text, **kwargs):
        self.sent.append((chat_id, text, kwargs))
        return SimpleNamespace(id=len(self.sent), text=text)

    async def edit_message_text(self, chat_id, message_id, text):
        self.calls.append(("edit", text))

    async def delete_messages(self, chat_id, message_ids):
        self.calls.append(("delete", message_ids))

    async def send_video(self, chat_id, video=None, **kwargs):
        self.calls.append(("video", video, kwargs.get("caption")))

    async def send_audio(self, chat_id, audio=None, **kwargs):
        self.calls.append(("audio", audio))

    async def send_voice(self, chat_id, voice=None, **kwargs):
        self.calls.append(("voice", voice))

    async def send_animation(self, chat_id, animation=None, **kwargs):
        self.calls.append(("animation", animation))

    async def send_photo(self, chat_id, photo=None, **kwargs):
        self.calls.append(("photo", photo))

    async def send_document(self, chat_id, document=None, **kwargs):
        self.calls.append(("document", document))

    async def send_sticker(self, chat_id, sticker=None, **kwargs):
        self.calls.append(("sticker", sticker))

    async def download_media(self, message, file_name=None):
        path = Path(file_name or "download.bin")
        path.write_bytes(b"file")
        return str(path)

    async def get_chat(self, target):
        self.calls.append(("get_chat", target))
        return SimpleNamespace(
            id=target if isinstance(target, int) else 77,
            type="private",
            first_name="Ada",
            last_name="Lovelace",
            username="ada",
            bio="math",
            members_count=None,
        )

    async def leave_chat(self, chat_id, delete=False):
        self.calls.append(("leave", chat_id, delete))

    async def update_profile(self, first_name=None, last_name=None, bio=None):
        self.calls.append(("profile", first_name, last_name, bio))
        return True

    async def set_profile_photo(self, *, photo=None, video=None):
        self.calls.append(("photo-set", photo))
        return True

    async def invoke(self, query):
        self.calls.append(("invoke", type(query).__name__))
        return SimpleNamespace(set=SimpleNamespace(title="Pack", short_name="tg5p1", count=3))

    async def get_me(self):
        return self.me


def _ctx(
    client,
    plugin_name,
    command,
    args="",
    *,
    reply=None,
    chat_type="supergroup",
    chat_id=50,
    account_id=3,
):
    return CommandContext(
        client=client,
        account_id=account_id,
        message=SimpleNamespace(
            outgoing=True,
            text=f".{command} {args}".strip(),
            id=20,
            chat=SimpleNamespace(id=chat_id, type=chat_type, title="Room"),
            from_user=SimpleNamespace(id=5, is_self=True),
            reply_to_message=reply,
        ),
        command=command,
        args=args,
        prefix=".",
        language="en",
        plugin_name=plugin_name,
        limiter=AccountLimiter(account_id, min_interval=0, retry_threshold=0),
    )


def _defaults(monkeypatch) -> None:
    monkeypatch.setattr(
        "src.runtime.plugins.PluginSettings.get",
        lambda self, key, default=None: default,
    )
    monkeypatch.setattr(
        "src.runtime.plugins.PluginSettings.set",
        lambda self, key, value: None,
    )


def test_phase4_names_are_in_the_default_grant_list():
    for name in PHASE4_PLUGIN_NAMES:
        assert name in DEFAULT_PLAN_PLUGINS
    assert DEFAULT_PLAN_PLUGINS.index("games") < DEFAULT_PLAN_PLUGINS.index("download")


def test_longest_phase4_names_win():
    pack = resolve_command(".معلومات الملصق", ".")
    info = resolve_command(".معلومات", ".")
    stop = resolve_command(".ايقاف التحميل", ".")
    mentions = resolve_command(".ايقاف التاك", ".")
    assert pack is not None and pack[0].meta.name == "stickers"
    assert info is not None and info[0].meta.name == "info"
    assert stop is not None and stop[1].name == "ايقاف التحميل"
    assert mentions is not None and mentions[1].name == "ايقاف التاك"


@pytest.mark.parametrize(
    "plugin_name",
    PHASE4_PLUGIN_NAMES,
)
def test_each_phase4_plugin_has_arabic_and_english(plugin_name):
    from src.runtime.plugins import get_plugin

    plugin = get_plugin(plugin_name)
    assert plugin is not None
    names = [command.name for command in plugin.meta.commands]
    assert any(not name.isascii() for name in names)
    assert any(name.isascii() for name in names)
    assert plugin.meta.description_ar
    assert plugin.meta.description_en


def test_public_urls_reject_local_targets():
    assert public_http_url("https://youtu.be/abc") == "https://youtu.be/abc"
    assert public_http_url("http://127.0.0.1/x") is None
    assert public_http_url("http://10.1.1.1/x") is None
    assert public_http_url("file:///etc/passwd") is None
    assert public_http_url("ftp://example.com/a") is None


def test_download_targets_and_options():
    video = classify_target("video", "lofi hip hop")
    assert video["ok"] and video["url"].startswith("ytsearch1:")
    direct = classify_target("audio", "https://youtu.be/abc")
    assert direct["audio"] is True and direct["mode"] == "url"
    assert classify_target("tiktok", "https://example.com/a")["error"] == "host"
    assert classify_target("instagram", "https://www.instagram.com/reel/1")["ok"]
    assert classify_target("url", "file:///tmp/a")["error"] == "url"
    assert classify_target("search", "https://youtu.be/abc")["error"] == "use_video"
    options = ytdlp_options("/tmp/tg", audio=False, max_bytes=1000, proxy="socks5://127.0.0.1:1")
    assert options["max_filesize"] == 1000
    assert options["concurrent_fragment_downloads"] == 1
    assert options["noplaylist"] is True
    assert "height<=720" in options["format"]
    assert options["proxy"] == "socks5://127.0.0.1:1"
    playlist = first_entry({"_type": "playlist", "entries": [{"id": "a"}, {"id": "b"}]})
    assert playlist is not None and playlist["id"] == "a"


def test_socks_url_keeps_credentials_out_of_the_path():
    url = format_socks_url(
        {"hostname": "proxy.example", "port": 1080, "username": "a b", "password": "p:a"}
    )
    assert url == "socks5://a%20b:p%3Aa@proxy.example:1080"
    assert (
        format_socks_url({"hostname": "proxy.example", "port": 1080})
        == "socks5://proxy.example:1080"
    )


def test_webp_resize_uses_one_side_of_512(tmp_path):
    from PIL import Image

    source = tmp_path / "wide.png"
    Image.new("RGB", (800, 200), "red").save(source)
    dest = tmp_path / "sticker.webp"
    result = to_webp({"src": str(source), "dest": str(dest)})
    assert result["ok"] is True
    assert result["width"] == 512
    assert dest.is_file()


def test_ffmpeg_gif_when_the_program_is_installed(tmp_path):
    import shutil
    import subprocess

    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg is not installed")
    source = tmp_path / "in.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=red:s=64x64:d=1",
            str(source),
        ],
        check=True,
        capture_output=True,
    )
    dest = tmp_path / "out.gif"
    result = convert(
        {
            "kind": "gif",
            "src": str(source),
            "dest": str(dest),
            "max_seconds": 5,
            "max_bytes": 5_000_000,
        }
    )
    assert result["ok"] is True
    assert dest.is_file()
    assert dest.stat().st_size > 0


def test_convert_and_ocr_report_missing_programs(monkeypatch, tmp_path):
    monkeypatch.setattr("src.media_jobs.shutil.which", lambda name: None)
    src = tmp_path / "in.bin"
    src.write_bytes(b"1234")
    gif = convert(
        {
            "kind": "gif",
            "src": str(src),
            "dest": str(tmp_path / "out.gif"),
            "max_seconds": 10,
            "max_bytes": 1000,
        }
    )
    assert gif["error"] == "missing"
    assert gif["detail"] == "ffmpeg"
    assert ocr({"src": str(src), "lang": "ara+eng"})["detail"] == "tesseract"


def test_calc_allows_arithmetic_only():
    assert safe_calc("2+2*3") == "8"
    assert safe_calc("(10-4)/2") == "3"
    with pytest.raises(CalcError):
        safe_calc("__import__('os').system('echo')")
    with pytest.raises(CalcError):
        safe_calc("1/0")
    with pytest.raises(CalcError):
        safe_calc("2**40")


def test_repeat_parser_caps_count_and_length():
    count, text, error = parse_repeat("٣ hello", "", cap=5)
    assert (count, text, error) == (3, "hello", "")
    assert parse_repeat("9 hi", "", cap=5)[2] == "cap"
    assert parse_repeat("2", "from reply", cap=5)[1] == "from reply"
    assert parse_repeat("1 " + ("x" * 400), "", cap=5)[2] == "long"
    assert parse_repeat("", "", cap=5)[2] == "missing"


def test_small_parsers():
    assert split_name("Ada Lovelace") == ("Ada", "Lovelace")
    assert split_name("  ") is None
    assert split_translate_args("en hello", "", "ar") == ("en", "hello")
    assert split_translate_args("", "مرحبا", "en") == ("en", "مرحبا")
    assert split_tts("ar hello", "", "en") == ("ar", "hello")
    assert pack_short_name(15, 2) == "tg15p2"
    assert pack_short_name(15, 2)[0].isalpha()
    assert pack_is_full(120)
    assert not pack_is_full(119)
    assert sticker_emoji("", SimpleNamespace(emoji="🔥")) == "🔥"
    assert can_leave("supergroup", -100, 5)
    assert not can_leave("private", 5, 5)
    info = format_chat_info(
        SimpleNamespace(
            id=7, type="channel", title="News", username="news", description="hi", members_count=3
        ),
        "en",
    )
    assert "News" in info and "@news" in info
    pack = format_pack(
        SimpleNamespace(set=SimpleNamespace(title="Pack", short_name="abc", count=2)), "ar"
    )
    assert "abc" in pack and "العدد" in pack
    from datetime import datetime
    from zoneinfo import ZoneInfo

    moment = datetime(2026, 9, 25, 14, 5, 6, tzinfo=ZoneInfo("UTC"))
    assert format_clock(moment, date=False).startswith("14:05:06")
    assert format_clock(moment, date=True) == "2026-09-25"


def test_media_workers_stay_at_one_or_two():
    with pytest.raises(ValidationError):
        Settings(media_workers=3)
    with pytest.raises(ValidationError):
        Settings(download_max_mb=500)


def test_grant_adds_phase4_without_removing_rows():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with engine.begin() as bind:
        bind.execute(
            text(
                "INSERT INTO plans (name, price_stars, price_usd, duration_days, "
                "max_accounts, daily_group_limit, is_active) "
                "VALUES ('Legacy', 1, 1, 30, 1, 1, 1)"
            )
        )
        bind.execute(text("INSERT INTO plan_plugins (plan_id, plugin_name) VALUES (1, 'ping')"))
        added = grant_named_plugins(bind, PHASE4_PLUGIN_NAMES)
        assert added == len(PHASE4_PLUGIN_NAMES)
        rows = {
            row[0]
            for row in bind.execute(text("SELECT plugin_name FROM plan_plugins WHERE plan_id = 1"))
        }
        assert "ping" in rows
        assert set(PHASE4_PLUGIN_NAMES) <= rows
        assert grant_named_plugins(bind, PHASE4_PLUGIN_NAMES) == 0
        revoke_named_plugins(bind, PHASE4_PLUGIN_NAMES)
        left = {
            row[0]
            for row in bind.execute(text("SELECT plugin_name FROM plan_plugins WHERE plan_id = 1"))
        }
    assert left == {"ping"}


async def test_pool_runs_in_a_child_and_can_be_cancelled():
    echoed = await run_named(91, "echo", {"value": 7}, slots=1)
    assert echoed == {"ok": True, "value": 7}
    cancel = asyncio.Event()
    task = asyncio.create_task(run_named(92, "sleep", {"seconds": 30}, slots=1, cancel=cancel))
    await asyncio.sleep(0.3)
    cancel.set()
    stopped = await asyncio.wait_for(task, timeout=20)
    assert stopped["error"] == "cancelled"


async def test_download_uploads_with_a_caption(monkeypatch):
    _defaults(monkeypatch)
    monkeypatch.setattr(
        "src.plugins.download.account_proxy_url", lambda account_id: "socks5://proxy:1"
    )

    async def fake_run(account_id, name, payload, **kwargs):
        assert payload["proxy"] == "socks5://proxy:1"
        assert payload["max_bytes"] == 50 * 1024 * 1024
        path = Path(payload["directory"]) / "clip.mp4"
        path.write_bytes(b"video")
        return {"ok": True, "path": str(path), "title": "Clip", "kind": "video", "audio": False}

    monkeypatch.setattr("src.runtime.work_pool.run_named", fake_run)
    client = FakeClient()
    await download_plugin.handle(_ctx(client, "download", "يوتيوب", "lofi", account_id=41))
    assert client.calls[-1][0] == "delete" or any(call[0] == "video" for call in client.calls)
    video = next(call for call in client.calls if call[0] == "video")
    assert video[2] == "Clip"
    assert "Downloading" in client.sent[0][1]


async def test_download_search_lists_results(monkeypatch):
    _defaults(monkeypatch)
    monkeypatch.setattr("src.plugins.download.account_proxy_url", lambda account_id: None)

    async def fake_run(account_id, name, payload, **kwargs):
        assert name == "search"
        return {
            "ok": True,
            "results": [{"title": "Song", "url": "https://youtu.be/a", "duration": 65}],
        }

    monkeypatch.setattr("src.runtime.work_pool.run_named", fake_run)
    client = FakeClient()
    await download_plugin.handle(_ctx(client, "download", "بحث", "song", account_id=42))
    assert any("Song" in call[1] for call in client.calls if call[0] == "edit")


async def test_download_missing_library_is_a_message(monkeypatch):
    _defaults(monkeypatch)
    monkeypatch.setattr("src.plugins.download.account_proxy_url", lambda account_id: None)

    async def fake_run(account_id, name, payload, **kwargs):
        return {"ok": False, "error": "missing", "detail": "yt-dlp"}

    monkeypatch.setattr("src.runtime.work_pool.run_named", fake_run)
    client = FakeClient()
    await download_plugin.handle(
        _ctx(client, "download", "تيك", "https://www.tiktok.com/t/1", account_id=43)
    )
    assert any("yt-dlp" in call[1] for call in client.calls if call[0] == "edit")


async def test_stickers_kang_and_pack_info(monkeypatch):
    _defaults(monkeypatch)

    async def fake_prepare(ctx, root):
        dest = root / "sticker.webp"
        dest.write_bytes(b"webp")
        return {"path": str(dest), "width": 512, "height": 512}

    async def fake_install(ctx, path, emoji, user_id, width, height):
        assert emoji == "🔥"
        assert user_id == 5
        return "tg5p1"

    monkeypatch.setattr("src.plugins.stickers._prepare_webp", fake_prepare)
    monkeypatch.setattr("src.plugins.stickers._install", fake_install)
    client = FakeClient()
    reply = SimpleNamespace(sticker=SimpleNamespace(emoji="🔥", is_animated=False, is_video=False))
    await stickers_plugin.handle(_ctx(client, "stickers", "ملصق", "", reply=reply, account_id=44))
    assert any(call[0] == "sticker" for call in client.calls)
    assert any("addstickers/tg5p1" in item[1] for item in client.sent)

    info_client = FakeClient()
    info_reply = SimpleNamespace(sticker=SimpleNamespace(set_name="tg5p1", is_animated=False))
    await stickers_plugin.handle(
        _ctx(info_client, "stickers", "معلومات الملصق", "", reply=info_reply, account_id=45)
    )
    assert "Pack" in info_client.sent[-1][1]
    assert info_client.calls[0][0] == "invoke"


async def test_sticker_to_image_refuses_animation():
    client = FakeClient()
    reply = SimpleNamespace(sticker=SimpleNamespace(is_animated=True, is_video=False))
    await stickers_plugin.handle(_ctx(client, "stickers", "لصورة", "", reply=reply, account_id=46))
    assert "Animated" in client.sent[-1][1]
    assert client.calls == []


async def test_translate_tts_ocr_and_convert(monkeypatch):
    _defaults(monkeypatch)

    async def fake_translate(text, target, source):
        return f"{source}>{target}:{text}"

    monkeypatch.setattr("src.plugins.translate.translate_text", fake_translate)
    client = FakeClient()
    await translate_plugin.handle(_ctx(client, "translate", "ترجمة", "en hello", account_id=47))
    assert client.sent[-1][1].endswith("en:hello") or "en:hello" in client.sent[-1][1]

    async def missing_tts(account_id, name, payload, **kwargs):
        assert name == "tts"
        return {"ok": False, "error": "missing", "detail": "gTTS"}

    monkeypatch.setattr("src.runtime.work_pool.run_named", missing_tts)
    voice = FakeClient()
    await tts_plugin.handle(_ctx(voice, "tts", "نطق", "hello", account_id=48))
    assert any("gTTS" in call[1] for call in voice.calls if call[0] == "edit")

    async def missing_ocr(account_id, name, payload, **kwargs):
        return {"ok": False, "error": "missing", "detail": "tesseract"}

    monkeypatch.setattr("src.runtime.work_pool.run_named", missing_ocr)
    reader = FakeClient()
    reply = SimpleNamespace(photo=SimpleNamespace(file_size=10))
    await ocr_plugin.handle(_ctx(reader, "ocr", "استخراج", "", reply=reply, account_id=49))
    assert any("tesseract" in call[1] for call in reader.calls if call[0] == "edit")

    async def missing_ffmpeg(account_id, name, payload, **kwargs):
        assert payload["kind"] == "gif"
        return {"ok": False, "error": "missing", "detail": "ffmpeg"}

    monkeypatch.setattr("src.runtime.work_pool.run_named", missing_ffmpeg)
    gif = FakeClient()
    await convert_plugin.handle(
        _ctx(
            gif,
            "convert",
            "لمتحرك",
            "",
            reply=SimpleNamespace(video=SimpleNamespace(file_size=10)),
            account_id=50,
        )
    )
    assert any("ffmpeg" in call[1] for call in gif.calls if call[0] == "edit")


async def test_telegraph_info_leave_repeat_profile_clock_calc(monkeypatch):
    _defaults(monkeypatch)

    async def fake_page(text, token):
        assert "note" in text
        return "token", "https://telegra.ph/note"

    monkeypatch.setattr("src.plugins.telegraph.publish_text", fake_page)
    page = FakeClient()
    await telegraph_plugin.handle(_ctx(page, "telegraph", "تليجراف", "note", account_id=51))
    assert page.sent[-1][1] == "https://telegra.ph/note"

    info = FakeClient()
    await info_plugin.handle(
        _ctx(info, "info", "معلومات", "", chat_type="private", chat_id=9, account_id=52)
    )
    assert "Ada" in info.sent[-1][1]
    assert info.calls[0] == ("get_chat", 9)

    private = FakeClient()
    await leave_plugin.handle(
        _ctx(private, "leave", "مغادرة", "", chat_type="private", chat_id=5, account_id=53)
    )
    assert private.calls == []
    group = FakeClient()
    await leave_plugin.handle(
        _ctx(group, "leave", "مغادرة", "", chat_type="supergroup", chat_id=-100, account_id=54)
    )
    assert ("leave", -100, False) in group.calls

    async def instant(_seconds):
        return None

    monkeypatch.setattr("src.plugins.repeat.asyncio.sleep", instant)
    repeat = FakeClient()
    await repeat_plugin.handle(_ctx(repeat, "repeat", "تكرار", "2 hello", account_id=55))
    assert [item[1] for item in repeat.sent] == ["hello", "hello"]
    capped = FakeClient()
    await repeat_plugin.handle(_ctx(capped, "repeat", "تكرار", "9 hello", account_id=56))
    assert "maximum" in capped.sent[-1][1].lower()
    command = FakeClient()
    await repeat_plugin.handle(_ctx(command, "repeat", "تكرار", "1 .ping", account_id=57))
    assert "not repeated" in command.sent[-1][1]

    profile = FakeClient()
    await profile_plugin.handle(
        _ctx(profile, "profile", "وضع الاسم", "Ada Lovelace", account_id=58)
    )
    assert ("profile", "Ada", "Lovelace", None) in profile.calls
    photo = FakeClient()
    await profile_plugin.handle(_ctx(photo, "profile", "وضع الصورة", "", account_id=59))
    assert "Reply" in photo.sent[-1][1]

    clock = FakeClient()
    await clock_plugin.handle(_ctx(clock, "clock", "الوقت", "", account_id=60))
    assert ":" in clock.sent[-1][1]
    day = FakeClient()
    await clock_plugin.handle(_ctx(day, "clock", "التاريخ", "", account_id=61))
    assert any(line[:4].isdigit() for line in day.sent[-1][1].splitlines())

    calc = FakeClient()
    await calc_plugin.handle(_ctx(calc, "calc", "احسب", "2+2", account_id=62))
    assert calc.sent[-1][1].rstrip().endswith("4")
    blocked = FakeClient()
    await calc_plugin.handle(_ctx(blocked, "calc", "احسب", "__import__('os')", account_id=63))
    assert "not allowed" in blocked.sent[-1][1]


def test_new_plans_include_phase4(tmp_path, monkeypatch):
    import src.database as database
    from src.runtime.gating import list_plugin_views

    monkeypatch.setattr(database, "DB_FILE", tmp_path / "bot.db")
    database.initialize_database()
    database.update_user_details(SimpleNamespace(id=111, first_name="Owner", username="owner"))
    assert database.add_plan("Phase4", 10, 1.0, 30, 5, 10)
    plan_id = database.get_all_plans()[0]["id"]
    assert database.grant_subscription(111, plan_id, 30)[0]
    profile = database.get_random_device_profile()
    assert database.add_managed_account(111, "+15550002222", "session-string", profile["id"])
    account_id = database.get_user_details(111)["accounts"][0]["id"]
    views = list_plugin_views(account_id, "en")
    assert views is not None
    enabled = {view["name"] for view in views if view["enabled"]}
    assert set(PHASE4_PLUGIN_NAMES) <= enabled
