"""CPU and network jobs that run in a short-lived child process.

Importing this module loads the standard library only. ``yt-dlp``, Pillow, and
gTTS are imported inside the job that needs them, then the process exits.
"""

from __future__ import annotations

import ipaddress
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

GIF_MAX_SECONDS = 15
TTS_MAX_CHARS = 400
STICKER_PACK_LIMIT = 120

_LANG = re.compile(r"^[A-Za-z]{2,3}([_+-][A-Za-z0-9]{2,8})*$")
_SKIP_SUFFIXES = {".part", ".ytdl", ".temp", ".tmp"}


def public_http_url(value: str) -> str | None:
    """Accept a public http(s) URL and reject local or private targets."""
    raw = value.strip()
    parsed = urlsplit(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return None
    host = parsed.hostname.lower().rstrip(".")
    if host in {"localhost", "localhost.localdomain"} or host.endswith(".local"):
        return None
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is not None and (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
    ):
        return None
    return raw


def classify_target(kind: str, text: str) -> dict[str, Any]:
    """Turn command text into a download or search target.

    ``kind`` is ``video``, ``audio``, ``search``, ``tiktok``, ``instagram``,
    or ``url``. The result is ``{ok, url, mode, audio, error}``.
    """
    raw = " ".join(text.split())
    if not raw:
        return {"ok": False, "error": "empty"}
    if kind == "search":
        if public_http_url(raw):
            return {"ok": False, "error": "use_video"}
        if len(raw) > 120:
            return {"ok": False, "error": "long"}
        return {"ok": True, "query": raw, "mode": "search"}
    if kind in {"tiktok", "instagram", "url"}:
        url = public_http_url(raw.split()[0])
        if url is None:
            return {"ok": False, "error": "url"}
        host = urlsplit(url).hostname or ""
        if kind == "tiktok" and "tiktok.com" not in host:
            return {"ok": False, "error": "host"}
        if kind == "instagram" and "instagram.com" not in host and "instagr.am" not in host:
            return {"ok": False, "error": "host"}
        return {"ok": True, "url": url, "mode": "url", "audio": False}
    if kind not in {"video", "audio"}:
        return {"ok": False, "error": "empty"}
    audio = kind == "audio"
    url = public_http_url(raw.split()[0]) if "://" in raw else None
    if url is not None:
        return {"ok": True, "url": url, "mode": "url", "audio": audio}
    if len(raw) > 120:
        return {"ok": False, "error": "long"}
    return {"ok": True, "url": f"ytsearch1:{raw}", "mode": "search", "audio": audio}


def ytdlp_options(
    directory: str,
    *,
    audio: bool,
    max_bytes: int,
    proxy: str | None,
) -> dict[str, Any]:
    """yt-dlp options that keep one small download on a small server."""
    limit = max(1, int(max_bytes))
    if audio:
        selected = f"bestaudio[filesize<={limit}]/bestaudio/best[filesize<={limit}]/best"
    else:
        selected = (
            f"bv*[height<=720][filesize<={limit}]+ba[filesize<={limit}]/"
            f"b[height<=720][filesize<={limit}]/b[filesize<={limit}]/b"
        )
    options: dict[str, Any] = {
        "outtmpl": str(Path(directory) / "%(id).80s.%(ext)s"),
        "restrictfilenames": True,
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "cachedir": False,
        "retries": 1,
        "fragment_retries": 1,
        "socket_timeout": 20,
        "concurrent_fragment_downloads": 1,
        "max_filesize": limit,
        "format": selected,
    }
    if proxy:
        options["proxy"] = proxy
    return options


def first_entry(info: dict[str, Any] | None) -> dict[str, Any] | None:
    if not info:
        return None
    entries = info.get("entries")
    if info.get("_type") in {"playlist", "multi_video"} or (
        isinstance(entries, list) and not info.get("formats")
    ):
        for entry in entries or []:
            if entry:
                return entry
        return None
    return info


def watch_url(entry: dict[str, Any], fallback: str) -> str:
    page = entry.get("webpage_url")
    if isinstance(page, str) and page.startswith("http"):
        return page
    raw = entry.get("url")
    if isinstance(raw, str) and raw.startswith("http"):
        return raw
    video_id = entry.get("id")
    extractor = str(entry.get("ie_key") or entry.get("extractor") or "")
    if video_id and "youtube" in extractor.lower():
        return f"https://www.youtube.com/watch?v={video_id}"
    return fallback


def newest_file(directory: str) -> Path | None:
    root = Path(directory)
    files = [
        path
        for path in root.iterdir()
        if path.is_file()
        and path.suffix.lower() not in _SKIP_SUFFIXES
        and not path.name.endswith(".part")
    ]
    if not files:
        return None
    return max(files, key=lambda path: path.stat().st_mtime)


def media_kind(path: Path, *, audio: bool) -> str:
    ext = path.suffix.lower()
    if audio:
        if ext in {".mp3", ".m4a", ".aac", ".flac", ".wav", ".opus", ".ogg"}:
            return "audio"
        return "document"
    if ext in {".mp4", ".mkv", ".webm", ".mov"}:
        return "video"
    if ext in {".jpg", ".jpeg", ".png", ".webp"}:
        return "photo"
    if ext == ".gif":
        return "animation"
    if ext in {".mp3", ".m4a", ".aac", ".flac", ".wav", ".opus", ".ogg"}:
        return "audio"
    return "document"


def _failed_download(exc: BaseException) -> dict[str, Any]:
    text = str(exc).lower()
    if "larger than max" in text or "max_filesize" in text or "file is larger" in text:
        return {"ok": False, "error": "size"}
    if "requested format is not available" in text:
        return {"ok": False, "error": "size"}
    return {"ok": False, "error": "failed"}


def download(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        import yt_dlp
    except ImportError:
        return {"ok": False, "error": "missing", "detail": "yt-dlp"}
    directory = str(payload["directory"])
    url = str(payload["url"])
    audio = bool(payload.get("audio"))
    max_bytes = int(payload["max_bytes"])
    max_seconds = int(payload["max_seconds"])
    proxy = payload.get("proxy") or None
    probe = ytdlp_options(directory, audio=audio, max_bytes=max_bytes, proxy=proxy)
    probe["skip_download"] = True
    try:
        with yt_dlp.YoutubeDL(probe) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as exc:
        return _failed_download(exc)
    entry = first_entry(info)
    if entry is None:
        return {"ok": False, "error": "empty"}
    duration = entry.get("duration") or 0
    try:
        too_long = int(duration) > max_seconds
    except (TypeError, ValueError):
        too_long = False
    if duration and too_long:
        return {"ok": False, "error": "duration"}
    target = watch_url(entry, url)
    options = ytdlp_options(directory, audio=audio, max_bytes=max_bytes, proxy=proxy)
    if audio and shutil.which("ffmpeg"):
        options["postprocessors"] = [
            {"key": "FFmpegExtractAudio", "preferredcodec": "m4a", "preferredquality": "128"}
        ]
    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            ydl.download([target])
    except Exception as exc:
        return _failed_download(exc)
    path = newest_file(directory)
    if path is None:
        return {"ok": False, "error": "empty"}
    if path.stat().st_size > max_bytes:
        path.unlink(missing_ok=True)
        return {"ok": False, "error": "size"}
    title = str(entry.get("title") or "").replace("\n", " ")[:200]
    return {
        "ok": True,
        "path": str(path),
        "title": title,
        "kind": media_kind(path, audio=audio),
        "audio": audio,
    }


def search(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        import yt_dlp
    except ImportError:
        return {"ok": False, "error": "missing", "detail": "yt-dlp"}
    query = str(payload.get("query") or "").strip()
    limit = max(1, min(int(payload.get("limit") or 5), 5))
    options: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": "in_playlist",
        "skip_download": True,
        "noplaylist": True,
        "cachedir": False,
        "socket_timeout": 20,
    }
    if payload.get("proxy"):
        options["proxy"] = payload["proxy"]
    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(f"ytsearch{limit}:{query}", download=False)
    except Exception:
        return {"ok": False, "error": "failed"}
    rows: list[dict[str, Any]] = []
    for entry in (info or {}).get("entries") or []:
        if not entry:
            continue
        url = entry.get("webpage_url") or entry.get("url") or ""
        if url and not str(url).startswith("http"):
            video_id = entry.get("id") or url
            url = f"https://www.youtube.com/watch?v={video_id}"
        rows.append(
            {
                "title": str(entry.get("title") or "")[:120],
                "url": str(url),
                "duration": entry.get("duration"),
            }
        )
        if len(rows) >= limit:
            break
    return {"ok": True, "results": rows}


def _run_ffmpeg(args: list[str], timeout: int) -> dict[str, Any] | None:
    if shutil.which("ffmpeg") is None:
        return {"ok": False, "error": "missing", "detail": "ffmpeg"}
    try:
        completed = subprocess.run(
            args,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "failed"}
    if completed.returncode != 0:
        return {"ok": False, "error": "failed"}
    return None


def convert(payload: dict[str, Any]) -> dict[str, Any]:
    kind = str(payload.get("kind") or "")
    src = str(payload["src"])
    dest = str(payload["dest"])
    limit = int(payload.get("max_seconds") or 60)
    if kind == "gif":
        limit = min(limit, GIF_MAX_SECONDS)
        command = [
            "ffmpeg",
            "-y",
            "-t",
            str(limit),
            "-i",
            src,
            "-vf",
            "fps=10,scale=320:-1:flags=lanczos",
            "-loop",
            "0",
            dest,
        ]
        out_kind = "animation"
    elif kind == "voice":
        command = [
            "ffmpeg",
            "-y",
            "-t",
            str(limit),
            "-i",
            src,
            "-vn",
            "-ac",
            "1",
            "-ar",
            "48000",
            "-c:a",
            "libopus",
            "-b:a",
            "48k",
            dest,
        ]
        out_kind = "voice"
    elif kind == "mp3":
        command = [
            "ffmpeg",
            "-y",
            "-t",
            str(limit),
            "-i",
            src,
            "-vn",
            "-b:a",
            "128k",
            dest,
        ]
        out_kind = "audio"
    else:
        return {"ok": False, "error": "failed"}
    failure = _run_ffmpeg(command, timeout=max(30, limit + 20))
    if failure is not None:
        return failure
    path = Path(dest)
    if not path.is_file() or path.stat().st_size == 0:
        return {"ok": False, "error": "empty"}
    max_bytes = int(payload.get("max_bytes") or 0)
    if max_bytes and path.stat().st_size > max_bytes:
        path.unlink(missing_ok=True)
        return {"ok": False, "error": "size"}
    return {"ok": True, "path": str(path), "kind": out_kind}


def _fit_sticker(image: Any) -> Any:
    image = image.convert("RGBA")
    image.thumbnail((512, 512))
    width, height = image.size
    if width <= 0 or height <= 0:
        raise ValueError("empty image")
    if width >= height:
        target = (512, max(1, int(height * 512 / width)))
    else:
        target = (max(1, int(width * 512 / height)), 512)
    if image.size != target:
        resample = image.Resampling.LANCZOS
        image = image.resize(target, resample)
    return image


def to_webp(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        from PIL import Image
    except ImportError:
        return {"ok": False, "error": "missing", "detail": "Pillow"}
    Image.MAX_IMAGE_PIXELS = 20_000_000
    src = str(payload["src"])
    dest = str(payload["dest"])
    try:
        with Image.open(src) as image:
            image.seek(0)
            fitted = _fit_sticker(image)
            width, height = fitted.size
            fitted.save(dest, "WEBP", quality=80, method=4)
    except Exception:
        return {"ok": False, "error": "failed"}
    return {"ok": True, "path": dest, "width": width, "height": height}


def to_jpeg(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        from PIL import Image
    except ImportError:
        return {"ok": False, "error": "missing", "detail": "Pillow"}
    Image.MAX_IMAGE_PIXELS = 20_000_000
    src = str(payload["src"])
    dest = str(payload["dest"])
    try:
        with Image.open(src) as image:
            image.seek(0)
            image.convert("RGB").save(dest, "JPEG", quality=85)
    except Exception:
        return {"ok": False, "error": "failed"}
    if not Path(dest).is_file():
        return {"ok": False, "error": "empty"}
    return {"ok": True, "path": dest, "kind": "photo"}


def video_frame(payload: dict[str, Any]) -> dict[str, Any]:
    failure = _run_ffmpeg(
        ["ffmpeg", "-y", "-i", str(payload["src"]), "-frames:v", "1", str(payload["dest"])],
        timeout=30,
    )
    if failure is not None:
        return failure
    if not Path(payload["dest"]).is_file():
        return {"ok": False, "error": "empty"}
    return {"ok": True, "path": str(payload["dest"]), "kind": "photo"}


def tts(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        from gtts import gTTS
    except ImportError:
        return {"ok": False, "error": "missing", "detail": "gTTS"}
    text = str(payload.get("text") or "").strip()
    if not text:
        return {"ok": False, "error": "empty"}
    lang = str(payload.get("lang") or "ar")
    if not re.fullmatch(r"[a-z]{2}(-[A-Za-z]{2})?", lang):
        return {"ok": False, "error": "lang"}
    directory = Path(str(payload["directory"]))
    mp3 = directory / "speech.mp3"
    try:
        gTTS(text=text[:TTS_MAX_CHARS], lang=lang).save(str(mp3))
    except Exception:
        return {"ok": False, "error": "failed"}
    ogg = directory / "speech.ogg"
    if shutil.which("ffmpeg"):
        failure = _run_ffmpeg(
            [
                "ffmpeg",
                "-y",
                "-i",
                str(mp3),
                "-vn",
                "-ac",
                "1",
                "-ar",
                "48000",
                "-c:a",
                "libopus",
                "-b:a",
                "48k",
                str(ogg),
            ],
            timeout=60,
        )
        if failure is None and ogg.is_file():
            return {"ok": True, "path": str(ogg), "kind": "voice"}
    return {"ok": True, "path": str(mp3), "kind": "audio"}


def ocr(payload: dict[str, Any]) -> dict[str, Any]:
    binary = shutil.which("tesseract")
    if binary is None:
        return {"ok": False, "error": "missing", "detail": "tesseract"}
    lang = str(payload.get("lang") or "ara+eng")
    if not _LANG.fullmatch(lang):
        lang = "ara+eng"
    try:
        completed = subprocess.run(
            [binary, str(payload["src"]), "stdout", "-l", lang, "--psm", "6"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "failed"}
    if completed.returncode != 0:
        return {"ok": False, "error": "failed"}
    text = (completed.stdout or "").strip()
    if not text:
        return {"ok": False, "error": "empty"}
    return {"ok": True, "text": text[:3500]}


def echo(payload: dict[str, Any]) -> dict[str, Any]:
    return {"ok": True, "value": payload.get("value")}


def sleep_job(payload: dict[str, Any]) -> dict[str, Any]:
    import time

    time.sleep(float(payload.get("seconds") or 1))
    return {"ok": True}


JOBS = {
    "download": download,
    "search": search,
    "convert": convert,
    "webp": to_webp,
    "jpeg": to_jpeg,
    "frame": video_frame,
    "tts": tts,
    "ocr": ocr,
    "echo": echo,
    "sleep": sleep_job,
}


def child_main(name: str, payload: dict[str, Any], queue: Any) -> None:
    func = JOBS.get(name)
    if func is None:
        queue.put({"ok": False, "error": "failed"})
        return
    try:
        queue.put(func(payload))
    except Exception:
        queue.put({"ok": False, "error": "failed"})
