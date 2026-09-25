"""List star-gift prices and send a gift only after a second command."""

from __future__ import annotations

import logging
import time
from typing import Any

from src.plugins.common import aliases, resolve_user, tr
from src.runtime.plugins import CommandContext, Plugin, PluginMeta

log = logging.getLogger(__name__)

_PENDING_SECONDS = 300
_pending: dict[int, dict[str, Any]] = {}


def format_gift_line(gift: Any, language: str) -> str:
    gift_id = getattr(gift, "id", "?")
    price = getattr(gift, "price", None)
    title = getattr(gift, "title", None) or ""
    flags: list[str] = []
    if getattr(gift, "is_sold_out", False):
        flags.append("نفد" if language == "ar" else "sold out")
    if getattr(gift, "is_limited", False):
        left = getattr(gift, "available_amount", None)
        flags.append(f"محدود {left}" if language == "ar" else f"limited {left}")
    extra = f" ({', '.join(flags)})" if flags else ""
    name = f" {title}" if title else ""
    return f"{gift_id}{name}: {price}⭐{extra}"


class GiftsPlugin(Plugin):
    meta = PluginMeta(
        name="gifts",
        description_en="List star gift prices and send a gift after a confirmation command.",
        description_ar="عرض أسعار هدايا النجوم وإرسال هدية بعد أمر تأكيد.",
        commands=(
            *aliases(
                "List gifts you can buy with Stars.",
                "عرض الهدايا التي يمكن شراؤها بالنجوم.",
                "اسعار الهدايا",
                "giftprices",
            ),
            *aliases(
                "Prepare a gift. Reply to the recipient or pass @user, then the gift id.",
                "تجهيز هدية. رد على المستلم أو أرسل @user ثم معرّف الهدية.",
                "ارسل هدية",
                "ارسل",
                "gift",
            ),
            *aliases(
                "Send the prepared gift. This spends Stars.",
                "إرسال الهدية المجهزة. هذا يخصم نجوماً.",
                "تأكيد الهدية",
                "giftconfirm",
            ),
            *aliases(
                "Discard the prepared gift.", "إلغاء الهدية المجهزة.", "الغاء الهدية", "giftcancel"
            ),
        ),
        default_enabled=True,
    )

    async def handle(self, ctx: CommandContext) -> None:
        if ctx.command in {"اسعار الهدايا", "giftprices"}:
            await _prices(ctx)
            return
        if ctx.command in {"تأكيد الهدية", "giftconfirm"}:
            await _confirm(ctx)
            return
        if ctx.command in {"الغاء الهدية", "giftcancel"}:
            _pending.pop(ctx.account_id, None)
            await ctx.reply(
                tr(ctx, "Gift cancelled. Nothing was sent.", "أُلغيت الهدية. لم يُرسل شيء.")
            )
            return
        await _prepare(ctx)


async def _prices(ctx: CommandContext) -> None:
    try:
        gifts = await ctx.limiter.run(ctx.client.get_available_gifts)
    except Exception:
        log.exception("Gift list failed for account %s", ctx.account_id)
        await ctx.reply(tr(ctx, "Could not load gift prices.", "تعذر تحميل أسعار الهدايا."))
        return
    balance = None
    get_balance = getattr(ctx.client, "get_stars_balance", None)
    if get_balance is not None:
        try:
            balance = await ctx.limiter.run(get_balance)
        except Exception:
            log.debug("Star balance failed", exc_info=True)
    lines = [format_gift_line(gift, ctx.language) for gift in list(gifts or [])[:30]]
    if not lines:
        await ctx.reply(tr(ctx, "No gifts are on sale.", "لا توجد هدايا معروضة."))
        return
    header = tr(ctx, "Gift prices", "أسعار الهدايا")
    if balance is not None:
        header += tr(ctx, f" — balance {balance}⭐", f" — الرصيد {balance}⭐")
    await ctx.reply(header + "\n" + "\n".join(lines))


async def _prepare(ctx: CommandContext) -> None:
    parts = (ctx.args or "").split()
    reply_user, _error = await resolve_user(ctx)
    gift_token = ""
    user: Any = reply_user
    if getattr(ctx.message, "reply_to_message", None) is not None:
        gift_token = parts[0] if parts else ""
    elif parts and parts[0].startswith("@"):
        user = parts[0]
        gift_token = parts[1] if len(parts) > 1 else ""
    elif len(parts) >= 2 and parts[0].lstrip("-").isdigit():
        user = int(parts[0])
        gift_token = parts[1]
    elif parts and parts[0].lstrip("-").isdigit() and reply_user is not None:
        gift_token = parts[0]
    if user is None or not gift_token.isdigit():
        await ctx.reply(
            tr(
                ctx,
                "Reply to someone with the gift id, or send @username and the gift id.",
                "رد على شخص مع معرّف الهدية، أو أرسل @username ومعرّف الهدية.",
            )
        )
        return
    gift_id = int(gift_token)
    price = await _lookup_price(ctx, gift_id)
    if price == "sold":
        await ctx.reply(tr(ctx, "That gift is sold out.", "هذه الهدية نفدت."))
        return
    _pending[ctx.account_id] = {
        "user": user,
        "gift_id": gift_id,
        "price": price,
        "at": time.monotonic(),
    }
    cost = price if isinstance(price, int) else "?"
    await ctx.reply(
        tr(
            ctx,
            f"Gift {gift_id} to {user} costs {cost}⭐. Nothing was sent. "
            f"Send {ctx.prefix}giftconfirm to spend Stars, or {ctx.prefix}giftcancel.",
            f"الهدية {gift_id} إلى {user} سعرها {cost}⭐. لم يُرسل شيء. "
            f"أرسل {ctx.prefix}تأكيد الهدية لخصم النجوم، أو {ctx.prefix}الغاء الهدية.",
        )
    )


async def _lookup_price(ctx: CommandContext, gift_id: int) -> int | str | None:
    try:
        gifts = await ctx.limiter.run(ctx.client.get_available_gifts)
    except Exception:
        return None
    for gift in gifts or []:
        if int(getattr(gift, "id", -1)) == gift_id:
            if getattr(gift, "is_sold_out", False):
                return "sold"
            price = getattr(gift, "price", None)
            return int(price) if isinstance(price, int) else None
    return None


async def _confirm(ctx: CommandContext) -> None:
    pending = _pending.get(ctx.account_id)
    if pending is None or time.monotonic() - float(pending["at"]) > _PENDING_SECONDS:
        _pending.pop(ctx.account_id, None)
        await ctx.reply(tr(ctx, "No gift is waiting.", "لا توجد هدية بانتظار التأكيد."))
        return
    _pending.pop(ctx.account_id, None)
    try:
        await ctx.limiter.run(
            ctx.client.send_gift,
            pending["user"],
            int(pending["gift_id"]),
        )
    except Exception:
        log.exception("Gift send failed for account %s", ctx.account_id)
        await ctx.reply(
            tr(
                ctx,
                "Telegram refused the gift. Stars were not confirmed spent.",
                "رفض تيليجرام الهدية.",
            )
        )
        return
    await ctx.reply(tr(ctx, "Gift sent.", "تم إرسال الهدية."))


plugin = GiftsPlugin()
