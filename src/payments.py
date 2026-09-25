"""Validation for Telegram Stars (XTR) invoices.

Stars amounts are whole star counts, not cents. Invoice payloads use
``plan_{plan_id}_user_{user_id}``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_PAYLOAD = re.compile(r"^plan_(\d+)_user_(\d+)$")

INVALID_PAYLOAD = "Invalid invoice payload"
WRONG_USER = "Invoice does not belong to this user"
USER_MISSING = "User is not registered. Send /start and try again"
PLAN_MISSING = "Plan not found"
PLAN_INACTIVE = "Plan is not active"
INVALID_PRICE = "Invalid plan price"
BAD_CURRENCY = "Unsupported currency"
AMOUNT_MISMATCH = "Amount does not match the plan price"


@dataclass(frozen=True)
class StarsPaymentCheck:
    ok: bool
    message: str
    plan_id: int | None = None


def parse_invoice_payload(payload: str | None) -> tuple[int, int] | None:
    if not payload:
        return None
    match = _PAYLOAD.fullmatch(payload.strip())
    if not match:
        return None
    return int(match.group(1)), int(match.group(2))


def validate_stars_payment(
    *,
    payload: str | None,
    currency: str | None,
    total_amount: int | None,
    payer_telegram_id: int,
    plan: dict | None,
    user_exists: bool,
) -> StarsPaymentCheck:
    """Return whether a Stars pre-checkout or successful payment may be granted."""
    parsed = parse_invoice_payload(payload)
    if parsed is None:
        return StarsPaymentCheck(False, INVALID_PAYLOAD)
    plan_id, payload_user_id = parsed
    if payload_user_id != payer_telegram_id:
        return StarsPaymentCheck(False, WRONG_USER, plan_id)
    if not user_exists:
        return StarsPaymentCheck(False, USER_MISSING, plan_id)
    if plan is None:
        return StarsPaymentCheck(False, PLAN_MISSING, plan_id)
    if not plan.get("is_active"):
        return StarsPaymentCheck(False, PLAN_INACTIVE, plan_id)
    price = plan.get("price_stars")
    if not isinstance(price, int) or isinstance(price, bool) or price <= 0:
        return StarsPaymentCheck(False, INVALID_PRICE, plan_id)
    if (currency or "").upper() != "XTR":
        return StarsPaymentCheck(False, BAD_CURRENCY, plan_id)
    if total_amount != price:
        return StarsPaymentCheck(False, AMOUNT_MISMATCH, plan_id)
    return StarsPaymentCheck(True, "", plan_id)
