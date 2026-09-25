"""Translate text with MyMemory or a configured LibreTranslate server."""

from __future__ import annotations

import logging
import re

from src.config import get_settings
from src.plugins.common import aliases, message_text, present, tr
from src.runtime.plugins import CommandContext, Plugin, PluginMeta, SettingField

log = logging.getLogger(__name__)

_CMD = ("ترجمة", "tr", "translate")
_LANG = re.compile(r"^[A-Za-z]{2}(-[A-Za-z]{2})?$")
_MAX = 450


def guess_source(text: str) -> str:
    arabic = sum(1 for character in text if "\u0600" <= character <= "\u06ff")
    latin = sum(1 for character in text if "a" <= character.lower() <= "z")
    if arabic > latin:
        return "ar"
    return "en"


def split_translate_args(args: str, reply: str, default_target: str) -> tuple[str, str] | None:
    """Return ``(target, text)`` or None when there is nothing to translate."""
    tokens = (args or "").strip().split(maxsplit=1)
    target = default_target or "en"
    text = ""
    if tokens and _LANG.fullmatch(tokens[0]):
        target = tokens[0].lower()
        text = tokens[1].strip() if len(tokens) > 1 else ""
    else:
        text = (args or "").strip()
    if not text:
        text = (reply or "").strip()
    if not text or not _LANG.fullmatch(target):
        return None
    return target, text[:_MAX]


class TranslatePlugin(Plugin):
    meta = PluginMeta(
        name="translate",
        description_en="Translate text with a free service. No API key by default.",
        description_ar="ترجمة نص عبر خدمة مجانية. لا مفتاح افتراضياً.",
        commands=(
            *aliases(
                "Translate text or a reply. Optional language code first.",
                "ترجمة نص أو رد. يمكن وضع رمز اللغة أولاً.",
                *_CMD,
            ),
        ),
        default_enabled=True,
        settings=(
            SettingField(
                "target",
                "Default target language",
                "لغة الترجمة الافتراضية",
                "str",
                "ar",
            ),
        ),
    )

    async def handle(self, ctx: CommandContext) -> None:
        reply = getattr(ctx.message, "reply_to_message", None)
        default = str(ctx.settings.get("target", "ar") or "ar")
        parsed = split_translate_args(ctx.args or "", message_text(reply) if reply else "", default)
        if parsed is None:
            await ctx.reply(
                present(
                    ctx,
                    "Use a language code and text, or reply to a message.",
                    "أرسل رمز اللغة والنص، أو رد على رسالة.",
                )
            )
            return
        target, text = parsed
        source = guess_source(text)
        if source == target:
            source = "en" if target == "ar" else "ar"
        try:
            translated = await translate_text(text, target, source)
        except Exception:
            log.info("translate failed account=%s", ctx.account_id)
            await ctx.reply(present(ctx, "Translation failed.", "فشلت الترجمة."))
            return
        if (
            len((ctx.args or "") + message_text(reply) if reply else "") > _MAX
            and len(text) >= _MAX
        ):
            note = tr(ctx, "\n(truncated)", "\n(مقتطع)")
        else:
            note = ""
        await ctx.reply(f"{translated}{note}"[:3900])


async def translate_text(text: str, target: str, source: str) -> str:
    settings = get_settings()
    provider = (settings.translate_provider or "mymemory").strip().lower()
    if provider == "libre" or settings.translate_url:
        return await _libre(text, target, source)
    return await _mymemory(text, target, source)


async def _mymemory(text: str, target: str, source: str) -> str:
    import httpx

    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.get(
            "https://api.mymemory.translated.net/get",
            params={"q": text[:_MAX], "langpair": f"{source}|{target}"},
        )
        response.raise_for_status()
        data = response.json()
    status = data.get("responseStatus") if isinstance(data, dict) else None
    translated = ""
    if isinstance(data, dict):
        translated = str((data.get("responseData") or {}).get("translatedText") or "")
    if status not in {200, "200"} or not translated:
        raise RuntimeError("mymemory")
    return translated


async def _libre(text: str, target: str, source: str) -> str:
    import httpx

    settings = get_settings()
    base = (settings.translate_url or "").rstrip("/")
    if not base:
        raise RuntimeError("libre url")
    payload: dict[str, str] = {
        "q": text[:_MAX],
        "source": source,
        "target": target,
        "format": "text",
    }
    if settings.translate_api_key:
        payload["api_key"] = settings.translate_api_key
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.post(f"{base}/translate", json=payload)
        response.raise_for_status()
        data = response.json()
    translated = str(data.get("translatedText") or "") if isinstance(data, dict) else ""
    if not translated:
        raise RuntimeError("libre")
    return translated


plugin = TranslatePlugin()
