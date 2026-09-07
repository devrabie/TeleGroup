"""Classify Telegram messages that should be forwarded as security alerts."""

from __future__ import annotations

import re
from typing import Optional

# Official Telegram service account that delivers login codes and security notices.
TELEGRAM_OFFICIAL_IDS = {777000}
TELEGRAM_OFFICIAL_USERNAMES = {"telegram"}

# Telegram login codes are typically 5 digits; other OTPs are commonly 5-8.
LOGIN_CODE_RE = re.compile(r"(?<!\d)(\d{5,8})(?!\d)")

# Phrases that indicate a verification / 2FA / login-security message.
# ASCII phrases are matched case-insensitively; Arabic phrases are matched as-is.
SECURITY_KEYWORDS = (
    "login code",
    "your login code",
    "verification code",
    "confirmation code",
    "authorization code",
    "authentication code",
    "two-step verification",
    "two-step verification password",
    "2-step verification",
    "2fa",
    "two-factor",
    "cloud password",
    "password was changed",
    "password has been changed",
    "password was disabled",
    "password was enabled",
    "password has been disabled",
    "new login",
    "new device",
    "unrecognized device",
    "active session",
    "do not give this code",
    "don't give this code",
    "do not share this code",
    "don't share this code",
    "we detected a login",
    "email code",
    "رمز الدخول",
    "رمز تسجيل الدخول",
    "كود التحقق",
    "رمز التحقق",
    "رمز التأكيد",
    "التحقق بخطوتين",
    "تم تغيير كلمة المرور",
    "تم تعطيل كلمة المرور",
    "تم تفعيل كلمة المرور",
    "جهاز جديد",
    "تسجيل دخول جديد",
    "لا تعط هذا الرمز",
    "لا تعطِ هذا الرمز",
    "لا تشارك هذا الرمز",
    "كلمة المرور السحابية",
    "كود تسجيل الدخول",
)

KIND_LOGIN_CODE = "login_code"
KIND_TWO_STEP = "two_step"
KIND_NEW_LOGIN = "new_login"
KIND_TELEGRAM_NOTICE = "telegram_notice"
KIND_VERIFICATION = "verification"


def extract_codes(text: str) -> list[str]:
    """Return unique 5-8 digit codes found in *text*, preserving order."""
    if not text:
        return []
    seen = set()
    codes = []
    for code in LOGIN_CODE_RE.findall(text):
        if code not in seen:
            seen.add(code)
            codes.append(code)
    return codes


def _has_security_keyword(text: str) -> bool:
    if not text:
        return False
    lowered = text.lower()
    for keyword in SECURITY_KEYWORDS:
        if keyword.isascii():
            if keyword.lower() in lowered:
                return True
        elif keyword in text:
            return True
    return False


def _kind_for_official(text: str, codes: list[str]) -> str:
    lowered = (text or "").lower()
    original = text or ""
    two_step_hints = (
        "two-step",
        "2-step",
        "2fa",
        "two-factor",
        "cloud password",
        "التحقق بخطوتين",
        "كلمة المرور السحابية",
        "كلمة المرور",
    )
    new_login_hints = (
        "new login",
        "new device",
        "unrecognized device",
        "we detected a login",
        "active session",
        "جهاز جديد",
        "تسجيل دخول جديد",
    )
    if any((h in lowered) if h.isascii() else (h in original) for h in two_step_hints) and not codes:
        return KIND_TWO_STEP
    if any((h in lowered) if h.isascii() else (h in original) for h in new_login_hints) and not codes:
        return KIND_NEW_LOGIN
    if codes:
        return KIND_LOGIN_CODE
    return KIND_TELEGRAM_NOTICE


def classify_security_message(
    *,
    text: str,
    from_user_id: Optional[int],
    username: Optional[str],
    is_private: bool,
    is_outgoing: bool,
) -> Optional[dict]:
    """
    Decide whether an incoming message should be forwarded to the account owner.

    Returns a dict with ``kind``, ``codes``, and ``from_official`` when it should
    be forwarded, otherwise ``None``.
    """
    if is_outgoing or not is_private:
        return None

    username_l = (username or "").lower().lstrip("@")
    from_official = (from_user_id in TELEGRAM_OFFICIAL_IDS) or (
        username_l in TELEGRAM_OFFICIAL_USERNAMES
    )
    codes = extract_codes(text or "")
    has_keyword = _has_security_keyword(text or "")

    if from_official:
        return {
            "kind": _kind_for_official(text or "", codes),
            "codes": codes,
            "from_official": True,
        }

    # Other private chats: only forward verification / 2FA style notices.
    if has_keyword:
        return {
            "kind": KIND_LOGIN_CODE if codes else KIND_VERIFICATION,
            "codes": codes,
            "from_official": False,
        }

    return None
