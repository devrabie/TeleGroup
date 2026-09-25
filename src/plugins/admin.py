"""Group admin actions: ban, kick, mute, promote, pin, and delete."""

from __future__ import annotations

import logging

from pyrogram.types import ChatPermissions, ChatPrivileges

from src.plugins.common import (
    aliases,
    chat_id_of,
    chat_kind,
    is_group_or_channel,
    present,
    resolve_user,
)
from src.runtime.plugins import CommandContext, Plugin, PluginMeta

log = logging.getLogger(__name__)

_BAN = ("حظر", "ban")
_UNBAN = ("الغاء الحظر", "unban")
_KICK = ("طرد", "kick")
_MUTE = ("كتم", "mute")
_UNMUTE = ("الغاء الكتم", "unmute")
_PROMOTE = ("رفع مشرف", "promote")
_DEMOTE = ("تنزيل مشرف", "demote")
_PIN = ("تثبيت", "pin")
_UNPIN = ("الغاء التثبيت", "unpin")
_DELETE = ("مسح", "del")
_PURGE = ("تنظيف", "purge")

_MAX_PURGE = 100

_MUTED = ChatPermissions(
    can_send_messages=False,
    can_send_media_messages=False,
    can_send_other_messages=False,
    can_send_polls=False,
    can_add_web_page_previews=False,
)
_OPEN = ChatPermissions(
    can_send_messages=True,
    can_send_media_messages=True,
    can_send_other_messages=True,
    can_send_polls=True,
    can_add_web_page_previews=True,
    can_invite_users=True,
)
_ADMIN = ChatPrivileges(
    can_manage_chat=True,
    can_delete_messages=True,
    can_restrict_members=True,
    can_invite_users=True,
    can_pin_messages=True,
    can_promote_members=False,
)
_MEMBER = ChatPrivileges(
    can_manage_chat=False,
    can_delete_messages=False,
    can_restrict_members=False,
    can_invite_users=False,
    can_pin_messages=False,
    can_promote_members=False,
)


def _describe(en: str, ar: str, names: tuple[str, ...]) -> tuple:
    return aliases(en, ar, *names)


class AdminPlugin(Plugin):
    meta = PluginMeta(
        name="admin",
        description_en="Ban, mute, promote, pin, and delete messages in groups you admin.",
        description_ar="حظر وكتم ورفع وتثبيت وحذف الرسائل في المجموعات التي تديرها.",
        commands=(
            *_describe(
                "Ban a user by reply, @username, or id.",
                "حظر مستخدم بالرد أو @username أو المعرّف.",
                _BAN,
            ),
            *_describe("Remove a ban.", "إلغاء الحظر.", _UNBAN),
            *_describe("Kick a user. They can rejoin.", "طرد مستخدم. يمكنه العودة.", _KICK),
            *_describe("Stop a user from sending messages.", "منع مستخدم من الإرسال.", _MUTE),
            *_describe("Allow a muted user to send again.", "السماح للمكتوم بالإرسال.", _UNMUTE),
            *_describe("Promote a user to admin.", "رفع مستخدم مشرفاً.", _PROMOTE),
            *_describe("Remove a user's admin rights.", "تنزيل مشرف.", _DEMOTE),
            *_describe("Pin the replied message.", "تثبيت الرسالة التي رُد عليها.", _PIN),
            *_describe("Unpin the replied message.", "إلغاء تثبيت الرسالة.", _UNPIN),
            *_describe("Delete the replied message.", "حذف الرسالة التي رُد عليها.", _DELETE),
            *_describe(
                "Delete messages from the reply up to this command (100 max).",
                "حذف الرسائل من الرد حتى هذا الأمر (100 كحد أقصى).",
                _PURGE,
            ),
        ),
        default_enabled=True,
    )

    async def handle(self, ctx: CommandContext) -> None:
        action = _ACTION.get(ctx.command)
        if action is None:
            return
        if not is_group_or_channel(ctx.message):
            await ctx.reply(
                present(ctx, "Use this in a group or channel.", "استخدم هذا في مجموعة أو قناة.")
            )
            return
        try:
            await action(ctx)
        except Exception:
            log.exception("Admin command %s failed for account %s", ctx.command, ctx.account_id)
            await ctx.reply(
                present(
                    ctx,
                    "Telegram refused that action.",
                    "رفض تيليجرام هذا الإجراء.",
                )
            )


async def _ban(ctx: CommandContext) -> None:
    user_id, error = await resolve_user(ctx)
    if error:
        await _need_target(ctx)
        return
    chat_id = chat_id_of(ctx.message)
    await ctx.limiter.run(ctx.client.ban_chat_member, chat_id, user_id)
    await ctx.reply(present(ctx, "Banned.", "تم الحظر."))


async def _unban(ctx: CommandContext) -> None:
    user_id, error = await resolve_user(ctx)
    if error:
        await _need_target(ctx)
        return
    chat_id = chat_id_of(ctx.message)
    await ctx.limiter.run(ctx.client.unban_chat_member, chat_id, user_id)
    await ctx.reply(present(ctx, "Ban removed.", "تم إلغاء الحظر."))


async def _kick(ctx: CommandContext) -> None:
    user_id, error = await resolve_user(ctx)
    if error:
        await _need_target(ctx)
        return
    chat_id = chat_id_of(ctx.message)
    await ctx.limiter.run(ctx.client.ban_chat_member, chat_id, user_id)
    await ctx.limiter.run(ctx.client.unban_chat_member, chat_id, user_id)
    await ctx.reply(present(ctx, "Kicked.", "تم الطرد."))


async def _mute(ctx: CommandContext) -> None:
    await _restrict(ctx, _MUTED, "Muted.", "تم الكتم.")


async def _unmute(ctx: CommandContext) -> None:
    await _restrict(ctx, _OPEN, "Unmuted.", "تم إلغاء الكتم.")


async def _restrict(ctx: CommandContext, permissions: ChatPermissions, en: str, ar: str) -> None:
    if chat_kind(ctx.message) == "channel":
        await ctx.reply(present(ctx, "Mute works in groups.", "الكتم يعمل في المجموعات."))
        return
    user_id, error = await resolve_user(ctx)
    if error:
        await _need_target(ctx)
        return
    await ctx.limiter.run(
        ctx.client.restrict_chat_member, chat_id_of(ctx.message), user_id, permissions
    )
    await ctx.reply(present(ctx, en, ar))


async def _promote(ctx: CommandContext) -> None:
    await _rank(ctx, _ADMIN, "Promoted.", "تم الرفع.")


async def _demote(ctx: CommandContext) -> None:
    await _rank(ctx, _MEMBER, "Demoted.", "تم التنزيل.")


async def _rank(ctx: CommandContext, privileges: ChatPrivileges, en: str, ar: str) -> None:
    user_id, error = await resolve_user(ctx)
    if error:
        await _need_target(ctx)
        return
    await ctx.limiter.run(
        ctx.client.promote_chat_member, chat_id_of(ctx.message), user_id, privileges
    )
    await ctx.reply(present(ctx, en, ar))


async def _pin(ctx: CommandContext) -> None:
    message_id = _target_message_id(ctx)
    if message_id is None:
        await ctx.reply(
            present(
                ctx,
                "Reply to the message you want to pin.",
                "رد على الرسالة التي تريد تثبيتها.",
            )
        )
        return
    await ctx.limiter.run(ctx.client.pin_chat_message, chat_id_of(ctx.message), message_id)
    await ctx.reply(present(ctx, "Pinned.", "تم التثبيت."))


async def _unpin(ctx: CommandContext) -> None:
    message_id = _target_message_id(ctx)
    if message_id is None:
        await ctx.reply(present(ctx, "Reply to the pinned message.", "رد على الرسالة المثبتة."))
        return
    await ctx.limiter.run(ctx.client.unpin_chat_message, chat_id_of(ctx.message), message_id)
    await ctx.reply(present(ctx, "Unpinned.", "تم إلغاء التثبيت."))


async def _delete(ctx: CommandContext) -> None:
    reply = getattr(ctx.message, "reply_to_message", None)
    reply_id = getattr(reply, "id", None)
    if reply_id is None:
        await ctx.reply(
            present(
                ctx,
                "Reply to the message you want to delete.",
                "رد على الرسالة التي تريد حذفها.",
            )
        )
        return
    await ctx.limiter.run(ctx.client.delete_messages, chat_id_of(ctx.message), [reply_id])
    await ctx.reply(present(ctx, "Deleted.", "تم الحذف."))


async def _purge(ctx: CommandContext) -> None:
    reply = getattr(ctx.message, "reply_to_message", None)
    start = getattr(reply, "id", None)
    end = getattr(ctx.message, "id", None)
    if start is None or end is None:
        await ctx.reply(
            present(ctx, "Reply to the first message to purge.", "رد على أول رسالة تريد تنظيفها.")
        )
        return
    if end < start or (end - start) > _MAX_PURGE:
        await ctx.reply(
            present(
                ctx,
                f"Purge covers at most {_MAX_PURGE} messages. Reply closer to this command.",
                f"التنظيف يحذف {_MAX_PURGE} رسالة كحد أقصى. رد على رسالة أقرب.",
            )
        )
        return
    ids = list(range(int(start), int(end))) or [int(start)]
    await ctx.limiter.run(ctx.client.delete_messages, chat_id_of(ctx.message), ids)
    await ctx.reply(present(ctx, f"Deleted {len(ids)} messages.", f"تم حذف {len(ids)} رسالة."))


def _target_message_id(ctx: CommandContext) -> int | None:
    reply = getattr(ctx.message, "reply_to_message", None)
    reply_id = getattr(reply, "id", None)
    if reply_id is not None:
        return int(reply_id)
    return None


async def _need_target(ctx: CommandContext) -> None:
    await ctx.reply(
        present(
            ctx,
            "Reply to a user, or pass @username or a numeric id.",
            "رد على المستخدم، أو أرسل @username أو المعرّف.",
        )
    )


_ACTION = {
    "حظر": _ban,
    "ban": _ban,
    "الغاء الحظر": _unban,
    "unban": _unban,
    "طرد": _kick,
    "kick": _kick,
    "كتم": _mute,
    "mute": _mute,
    "الغاء الكتم": _unmute,
    "unmute": _unmute,
    "رفع مشرف": _promote,
    "promote": _promote,
    "تنزيل مشرف": _demote,
    "demote": _demote,
    "تثبيت": _pin,
    "pin": _pin,
    "الغاء التثبيت": _unpin,
    "unpin": _unpin,
    "مسح": _delete,
    "del": _delete,
    "تنظيف": _purge,
    "purge": _purge,
}

plugin = AdminPlugin()
