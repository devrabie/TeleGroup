"""Let the owner add people who may command this account."""

from __future__ import annotations

import logging

from src.plugins.common import aliases, present
from src.runtime.plugins import CommandContext, Plugin, PluginMeta
from src.runtime.store import (
    MAX_ACCOUNT_ADMINS,
    add_account_admin,
    list_account_admins,
    remove_account_admin,
)
from src.templates import card, field, section

log = logging.getLogger(__name__)

_ADD = ("رفع ادمن", "addadmin")
_REMOVE = ("تنزيل ادمن", "deladmin")
_LIST = ("الادمنية", "admins")


class DelegatesPlugin(Plugin):
    meta = PluginMeta(
        name="delegates",
        description_en="Choose people who may send this account's commands from their own account.",
        description_ar="اختيار أشخاص يرسلون أوامر هذا الحساب من حساباتهم.",
        commands=(
            *aliases(
                "Add an admin by reply, numeric id, or @username. Owner only.",
                "إضافة مسؤول بالرد أو المعرّف أو @username. لصاحب الحساب فقط.",
                *_ADD,
            ),
            *aliases(
                "Remove an admin by reply, numeric id, or @username. Owner only.",
                "إزالة مسؤول بالرد أو المعرّف أو @username. لصاحب الحساب فقط.",
                *_REMOVE,
            ),
            *aliases(
                "List the people who may command this account.",
                "عرض الأشخاص الذين يستطيعون إرسال أوامر هذا الحساب.",
                *_LIST,
            ),
        ),
        default_enabled=True,
    )

    async def handle(self, ctx: CommandContext) -> None:
        if ctx.command in _LIST:
            await ctx.reply(_listing(ctx))
            return
        if ctx.actor_role == "admin":
            await ctx.reply(
                present(
                    ctx,
                    "Only the account owner can add or remove admins.",
                    "إضافة المسؤولين وإزالتهم لصاحب الحساب فقط.",
                )
            )
            return
        if ctx.command in _REMOVE:
            await _remove(ctx)
            return
        await _add(ctx)


def _listing(ctx: CommandContext) -> str:
    rows = list_account_admins(ctx.account_id)
    title = section("المسؤولون" if ctx.language == "ar" else "Admins")
    if not rows:
        empty = (
            "لا يوجد مسؤولون بعد.\n"
            f"أضف واحداً بـ {ctx.prefix}رفع ادمن بالرد، أو بالمعرّف، أو بـ @username."
            if ctx.language == "ar"
            else (
                "No admins yet.\n"
                f"Add one with {ctx.prefix}addadmin by reply, numeric id, or @username."
            )
        )
        return card(ctx.language, title, empty)
    lines = [
        (
            "هؤلاء يرسلون أوامر الحساب من حساباتهم. الأوامر الحساسة تبقى لك."
            if ctx.language == "ar"
            else "These people can command this account. Sensitive commands stay with you."
        )
    ]
    for row in rows:
        username = row.get("username")
        shown = f"@{username}" if username else "—"
        lines.append(field(str(row["telegram_id"]), shown))
    return card(ctx.language, title, "\n".join(lines))


async def _target(ctx: CommandContext) -> tuple[int | None, str | None, str]:
    """Return ``(telegram id, username, error)``. ``error`` is empty on success."""
    reply = getattr(ctx.message, "reply_to_message", None)
    if reply is not None:
        user = getattr(reply, "from_user", None)
        user_id = getattr(user, "id", None)
        if user is not None and user_id is not None:
            if getattr(user, "is_bot", False):
                return None, None, "bot"
            username = getattr(user, "username", None)
            return int(user_id), str(username) if username else None, ""
        return None, None, "missing"
    token = (ctx.args or "").strip().split()[0] if ctx.args else ""
    if not token:
        return None, None, "missing"
    if token.startswith("@"):
        if len(token) < 2:
            return None, None, "missing"
        try:
            found = await ctx.limiter.run(ctx.client.get_users, token)
        except Exception:
            log.debug("Could not resolve %s for account %s", token, ctx.account_id)
            return None, None, "missing"
        if isinstance(found, list):
            found = found[0] if found else None
        found_id = getattr(found, "id", None)
        if found is None or found_id is None:
            return None, None, "missing"
        if getattr(found, "is_bot", False):
            return None, None, "bot"
        username = getattr(found, "username", None)
        return int(found_id), str(username) if username else None, ""
    number = token[1:] if token.startswith("-") else token
    if number.isdigit() and not token.startswith("-"):
        return int(token), None, ""
    return None, None, "missing"


def _me_id(ctx: CommandContext) -> int | None:
    me = getattr(ctx.client, "me", None)
    me_id = getattr(me, "id", None)
    if me_id is None:
        return None
    return int(me_id)


def _reject_special(ctx: CommandContext, user_id: int) -> str | None:
    from src.runtime.store import get_plugin_account

    account = get_plugin_account(ctx.account_id)
    owner = None if account is None else account.get("telegram_id")
    if owner is not None and int(owner) == user_id:
        return present(
            ctx,
            "The account owner can already send commands.",
            "صاحب الحساب يستطيع إرسال الأوامر دون إضافته.",
        )
    me_id = _me_id(ctx)
    if me_id is not None and me_id == user_id:
        return present(
            ctx,
            "This account can already send its own commands.",
            "هذا الحساب يرسل أوامره دون إضافته كمسؤول.",
        )
    return None


async def _add(ctx: CommandContext) -> None:
    user_id, username, error = await _target(ctx)
    if error == "bot":
        await ctx.reply(present(ctx, "A bot cannot be an admin.", "لا يمكن إضافة بوت."))
        return
    if error or user_id is None:
        await ctx.reply(
            present(
                ctx,
                f"Reply to the person, or send {ctx.prefix}addadmin 123456 or @username.",
                f"رد على الشخص، أو أرسل {ctx.prefix}رفع ادمن 123456 أو @username.",
            )
        )
        return
    special = _reject_special(ctx, user_id)
    if special is not None:
        await ctx.reply(special)
        return
    result = add_account_admin(
        ctx.account_id,
        user_id,
        username=username,
        added_by_telegram_id=ctx.actor_id,
    )
    messages = {
        "added": (
            "Admin added. They can send commands from their own account.",
            "تمت إضافة المسؤول. يستطيع الآن إرسال الأوامر من حسابه.",
        ),
        "exists": ("That person is already an admin.", "هذا المستخدم مسؤول بالفعل."),
        "owner": (
            "The account owner can already send commands.",
            "صاحب الحساب يستطيع إرسال الأوامر دون إضافته.",
        ),
        "full": (
            f"This account already has {MAX_ACCOUNT_ADMINS} admins.",
            f"اكتمل العدد. الحد {MAX_ACCOUNT_ADMINS} مسؤولاً.",
        ),
        "invalid": ("That id is not valid.", "هذا المعرّف غير صالح."),
        "missing": ("Could not add that admin.", "تعذرت إضافة المسؤول."),
    }
    en, ar = messages.get(result, messages["missing"])
    await ctx.reply(present(ctx, en, ar))


async def _remove(ctx: CommandContext) -> None:
    user_id, _username, error = await _target(ctx)
    if error == "bot" or error or user_id is None:
        await ctx.reply(
            present(
                ctx,
                f"Reply to the admin, or send {ctx.prefix}deladmin 123456 or @username.",
                f"رد على المسؤول، أو أرسل {ctx.prefix}تنزيل ادمن 123456 أو @username.",
            )
        )
        return
    if remove_account_admin(ctx.account_id, user_id):
        await ctx.reply(present(ctx, "Admin removed.", "تمت إزالة المسؤول."))
        return
    await ctx.reply(present(ctx, "That person is not an admin.", "هذا المستخدم ليس مسؤولاً."))


plugin = DelegatesPlugin()
