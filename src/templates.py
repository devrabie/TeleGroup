"""Shared reply layout for userbot commands and the inline panel.

Arabic is the primary copy. Any other owner language uses the English text.
Edit the chrome here; command usage lives in ``src.command_catalog``.
"""

from __future__ import annotations

SEP = "⋆┄─┄─┄─┄┄─┄─┄─┄─┄┄⋆"

_TONE_ICON = {"ok": "✅", "err": "❌", "wait": "⏳", "info": "📌"}
_TONE_TITLE = {
    "ok": ("تم", "Done"),
    "err": ("تنبيه", "Notice"),
    "wait": ("انتظر", "Please wait"),
    "info": ("ملاحظة", "Note"),
}


def is_ar(language: str) -> bool:
    return language == "ar"


def pick(language: str, en: str, ar: str) -> str:
    if is_ar(language):
        return ar
    return en


def guess_tone(english: str) -> str:
    """Choose a frame from the English source sentence."""
    lowered = english.strip().casefold()
    if lowered.endswith("…") or lowered.endswith("..."):
        return "wait"
    errors = (
        "fail",
        "could not",
        "refused",
        "not ",
        "no ",
        "send ",
        "reply",
        "use ",
        "pick ",
        "choose",
        "too ",
        "larger",
        "missing",
        "invalid",
        "full",
        "zero",
        "allowed",
        "works in",
        "inside",
        "write ",
        "open the",
        "nothing",
        "already",
        "maximum",
        "limit is",
        "error",
    )
    if any(marker in lowered for marker in errors):
        return "err"
    return "ok"


def tone_line(language: str, tone: str, text: str) -> str:
    """One status message: header, separator, then the sentence."""
    icon = _TONE_ICON.get(tone, _TONE_ICON["info"])
    ar_title, en_title = _TONE_TITLE.get(tone, _TONE_TITLE["info"])
    title = ar_title if is_ar(language) else en_title
    body = text.strip()
    return f"𓆩 {icon} {title} 𓆪\n{SEP}\n{body}"


def card(language: str, title: str, body: str) -> str:
    del language
    return f"𓆩 {title} 𓆪\n{SEP}\n{body.strip()}"


def field(label: str, value: object) -> str:
    return f"● {label}: {value}"


def section(title: str) -> str:
    return f"𓆩 {title} 𓆪"


def result_card(language: str, value: str) -> str:
    title = "النتيجة" if is_ar(language) else "Result"
    return card(language, title, value)


def inline_disabled(language: str, prefix: str) -> str:
    if is_ar(language):
        body = (
            "الوضع الإنلاين لبوت التحكم غير مفعّل، لذلك لا يمكن إرسال لوحة الأزرار.\n"
            "\n"
            "فعّله من @BotFather:\n"
            "1. أرسل /setinline\n"
            "2. اختر بوت التحكم\n"
            "3. أرسل النص الذي يظهر في البحث، مثل: تحكم\n"
            "\n"
            f"ثم أعد إرسال {prefix}تحكم"
        )
        return card(language, "❌ تنبيه", body)
    body = (
        "Inline mode is off for the control bot, so the button panel cannot be posted.\n"
        "\n"
        "Turn it on with @BotFather:\n"
        "1. Send /setinline\n"
        "2. Choose the control bot\n"
        "3. Send the placeholder people see while searching, for example: panel\n"
        "\n"
        f"Then send {prefix}panel again"
    )
    return card(language, "❌ Notice", body)


def panel_failed(language: str, prefix: str) -> str:
    if is_ar(language):
        body = (
            "تعذر فتح اللوحة.\n"
            "تأكد أن بوت التحكم يعمل، ثم أعد المحاولة.\n"
            "إذا لم تفعّل الوضع الإنلاين من قبل، أرسل /setinline إلى @BotFather "
            f"ثم أعد {prefix}تحكم"
        )
        return card(language, "❌ تنبيه", body)
    body = (
        "The panel could not be opened.\n"
        "Check that the control bot is running, then try again.\n"
        "If inline mode was never enabled, send /setinline to @BotFather "
        f"and retry {prefix}panel"
    )
    return card(language, "❌ Notice", body)


def help_intro(language: str, prefix: str) -> str:
    """Prefix line for the command index. Who-may-send lives in the system section."""
    if is_ar(language):
        return f"البادئة: {prefix}"
    return f"Prefix: {prefix}"


def owner_only_notice(language: str) -> str:
    return tone_line(
        language,
        "err",
        pick(
            language,
            "Only the account owner can use this command.",
            "هذا الأمر لصاحب الحساب فقط. المسؤول لا يستخدمه.",
        ),
    )


def rate_limit_notice(language: str) -> str:
    return tone_line(
        language,
        "wait",
        pick(
            language,
            "Too many commands in a short time. Wait a moment, then try again.",
            "أُرسلت أوامر كثيرة خلال وقت قصير. انتظر قليلاً ثم أعد المحاولة.",
        ),
    )


def command_help_card(
    language: str,
    *,
    prefix: str,
    title: str,
    description: str,
    usage: str,
    example: str,
    aliases: str,
    status: str = "",
    access: str = "",
) -> str:
    del prefix
    if is_ar(language):
        rows = [
            field("الوصف", description),
            field("الاستخدام", usage),
            field("مثال", example),
        ]
        if aliases:
            rows.append(field("الأسماء", aliases))
        if status:
            rows.append(field("الحالة", status))
        if access:
            rows.append(field("من يستخدمه", access))
        return card(language, title, "\n".join(rows))
    rows = [
        field("Description", description),
        field("Usage", usage),
        field("Example", example),
    ]
    if aliases:
        rows.append(field("Names", aliases))
    if status:
        rows.append(field("Status", status))
    if access:
        rows.append(field("Who can use it", access))
    return card(language, title, "\n".join(rows))


def panel_home(language: str, prefix: str) -> str:
    if is_ar(language):
        body = (
            "من هنا تدير ميزات الحساب وتقرأ أوامرها.\n"
            "اضغط قسماً، ثم اسم الميزة، لتشغيلها أو إيقافها أو رؤية مثال.\n"
            f"● أوامر الحساب تبدأ بـ {prefix}\n"
            f"● لإعادة الفتح: {prefix}تحكم\n"
            "🟢 تعمل الآن · ⚪️ متوقفة · 🔒 غير متاحة في خطتك\n"
            "زر «المسؤولون» يعرض من يستطيع إرسال الأوامر غيرك."
        )
        return card(language, "🎛 لوحة التحكم", body)
    body = (
        "Manage this account's features and read their commands.\n"
        "Tap a section, then a feature, to turn it on or off or see an example.\n"
        f"● Commands start with {prefix}\n"
        f"● Open it again with {prefix}panel\n"
        "🟢 On · ⚪️ Off · 🔒 Not in your plan\n"
        "The admins button lists who else may send commands."
    )
    return card(language, "🎛 Control panel", body)


def panel_denied(language: str) -> str:
    return pick(
        language,
        "Only the account owner can use this button.",
        "هذا الزر لصاحب الحساب فقط.",
    )


def panel_expired(language: str) -> str:
    return pick(
        language,
        "This button expired. Open the panel again.",
        "انتهت صلاحية هذا الزر. أعد فتح اللوحة.",
    )


def panel_closed(language: str) -> str:
    if is_ar(language):
        return card(language, "تم الإغلاق", "أُغلقت لوحة التحكم.")
    return card(language, "Closed", "The control panel was closed.")


def apply_prefix(text: str, prefix: str) -> str:
    return text.replace("{p}", prefix)
