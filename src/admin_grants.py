"""Admin screens for manual plan grants and one-time activation links."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from html import escape
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import (
    BaseHandler,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from src import config
from src.database import get_all_plans, get_plan_by_id
from src.subscription_grants import (
    CODE_PAGE_SIZE,
    ERR_CHOOSE_MODE,
    ERR_DB,
    ERR_EXPIRED,
    ERR_INVALID_CODE,
    ERR_INVALID_TARGET,
    ERR_NO_SUBSCRIPTION,
    ERR_NOT_REVOCABLE,
    ERR_REVOKED,
    ERR_USED,
    ERR_USERNAME_UNKNOWN,
    SEARCH_PAGE_SIZE,
    GrantMode,
    RedeemOutcome,
    apply_plan_grant,
    get_activation_code,
    issue_activation_codes,
    list_activation_codes,
    load_grant_target,
    parse_batch_count,
    parse_duration_days,
    parse_link_expiry,
    resolve_grant_target,
    revoke_activation_code,
    revoke_subscription,
    search_users,
)
from src.subscription_grants import (
    activation_link as build_activation_link,
)
from src.sharing import bot_username, format_person
from src.translation import get_translation_func_for_user

log = logging.getLogger(__name__)

(
    GRANT_MENU,
    GRANT_LOOKUP,
    GRANT_SEARCH,
    GRANT_RESULTS,
    GRANT_USER,
    GRANT_PLAN,
    GRANT_DAYS,
    GRANT_MODE,
) = range(60, 68)
(
    CODE_MENU,
    CODE_PLAN,
    CODE_DAYS,
    CODE_COUNT,
    CODE_EXPIRY,
) = range(70, 75)

_GRANT_KEYS = (
    "grant_target",
    "grant_plan_id",
    "grant_days",
    "grant_search",
    "grant_default_days",
)
_CODE_KEYS = ("code_plan_id", "code_days", "code_count", "code_default_days")


def _tr(update: Update):
    user = update.effective_user
    return get_translation_func_for_user(user.id if user else 0)


def _is_admin(update: Update) -> bool:
    user = update.effective_user
    return user is not None and user.id in config.ADMIN_IDS


def _esc(value: object) -> str:
    return escape(str(value), quote=False)


def _pretty(iso_value: str | None) -> str:
    if not iso_value:
        return "—"
    text = iso_value.replace("T", " ").replace("+00:00", " UTC")
    if "." in text:
        head, tail = text.split(".", 1)
        zone = ""
        if " UTC" in tail:
            zone = " UTC"
        return head + zone
    return text


def _clear(context: ContextTypes.DEFAULT_TYPE, keys: tuple[str, ...]) -> None:
    data = context.user_data
    if data is None:
        return
    for key in keys:
        data.pop(key, None)


def _kb(rows: list[list[InlineKeyboardButton]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(rows)


async def _show(
    update: Update,
    text: str,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> None:
    query = update.callback_query
    if query is not None:
        await query.answer()
        await query.edit_message_text(
            text,
            reply_markup=reply_markup,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
        return
    message = update.effective_message
    if message is not None:
        await message.reply_text(
            text,
            reply_markup=reply_markup,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )


async def _admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    from src.admin_handlers import admin_panel_handler

    await admin_panel_handler(update, context)


def _duration_rows(
    prefix: str, default_days: int, cancel_data: str, _
) -> list[list[InlineKeyboardButton]]:
    return [
        [
            InlineKeyboardButton(
                _("Plan default ({days} days)").format(days=default_days),
                callback_data=f"{prefix}def",
            )
        ],
        [
            InlineKeyboardButton(_("7 days"), callback_data=f"{prefix}7"),
            InlineKeyboardButton(_("30 days"), callback_data=f"{prefix}30"),
            InlineKeyboardButton(_("90 days"), callback_data=f"{prefix}90"),
        ],
        [InlineKeyboardButton(_("✏️ Custom days"), callback_data=f"{prefix}custom")],
        [InlineKeyboardButton(_("❌ Cancel"), callback_data=cancel_data)],
    ]


def _plan_rows(prefix: str, cancel_data: str, _) -> list[list[InlineKeyboardButton]] | None:
    plans = get_all_plans(active_only=True)
    if not plans:
        return None
    rows = [
        [
            InlineKeyboardButton(
                _("{name} — {days} days").format(name=plan["name"], days=plan["duration_days"]),
                callback_data=f"{prefix}{plan['id']}",
            )
        ]
        for plan in plans
    ]
    rows.append([InlineKeyboardButton(_("❌ Cancel"), callback_data=cancel_data)])
    return rows


def grant_notice(plan_name: str, expiry: str, translate) -> str:
    return translate(
        "✅ An admin activated the <b>{plan}</b> plan for you.\nIt is valid until <b>{expiry}</b>."
    ).format(plan=_esc(plan_name), expiry=_esc(expiry))


def activation_reply(outcome: RedeemOutcome, translate) -> str:
    if outcome.ok:
        return translate("✅ The <b>{plan}</b> plan is active until <b>{expiry}</b>.").format(
            plan=_esc(outcome.plan_name or ""), expiry=_esc(_pretty(outcome.end_date))
        )
    if outcome.error == ERR_USED:
        return translate("This activation code has already been used.")
    if outcome.error == ERR_EXPIRED:
        return translate("This activation code has expired.")
    if outcome.error == ERR_REVOKED:
        return translate("This activation code has been revoked.")
    if outcome.error == ERR_INVALID_CODE:
        return translate("This activation code is not valid.")
    return translate("Could not activate this code. Please try again later.")


def _grant_menu_text(_) -> str:
    return _("<b>Grant a plan</b>\n\nSend a numeric Telegram ID, an @username, or search by name.")


def _grant_menu_keyboard(_) -> InlineKeyboardMarkup:
    return _kb(
        [
            [InlineKeyboardButton(_("🔢 Telegram ID or @username"), callback_data="admin_glookup")],
            [InlineKeyboardButton(_("🔎 Search by name"), callback_data="admin_gsearch")],
            [InlineKeyboardButton(_("❌ Cancel"), callback_data="admin_grant_cancel")],
        ]
    )


async def grant_open(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not _is_admin(update):
        return ConversationHandler.END
    _clear(context, _GRANT_KEYS)
    _ = _tr(update)
    await _show(update, _grant_menu_text(_), _grant_menu_keyboard(_))
    return GRANT_MENU


async def grant_ask_lookup(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not _is_admin(update):
        return ConversationHandler.END
    _ = _tr(update)
    await _show(
        update,
        _(
            "Send the user's numeric Telegram ID or @username.\n\n"
            "If they have never pressed Start, a numeric ID still works. "
            "An @username works only after they have opened the bot.\n\n"
            "Send /cancel to stop."
        ),
    )
    return GRANT_LOOKUP


async def grant_ask_search(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not _is_admin(update):
        return ConversationHandler.END
    _ = _tr(update)
    await _show(
        update,
        _("Send part of the user's name or username.\n\nSend /cancel to stop."),
    )
    return GRANT_SEARCH


async def grant_receive_lookup(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not _is_admin(update):
        return ConversationHandler.END
    _ = _tr(update)
    text = (update.message.text if update.message else "") or ""
    target = resolve_grant_target(text)
    message = update.message
    if message is None:
        return GRANT_LOOKUP
    if target.status == ERR_INVALID_TARGET:
        await message.reply_text(_("That is not a Telegram ID or @username."))
        return GRANT_LOOKUP
    if target.status == ERR_USERNAME_UNKNOWN:
        await message.reply_text(
            _(
                "No user with username <b>{username}</b> has started the bot, "
                "so there is no Telegram ID to attach a plan to. "
                "Ask them to press Start, or send their numeric Telegram ID."
            ).format(username=_esc(target.username or text)),
            parse_mode=ParseMode.HTML,
        )
        return GRANT_LOOKUP
    if target.status == ERR_DB or target.telegram_id is None:
        await message.reply_text(_("An error occurred. Please try again."))
        return GRANT_LOOKUP
    return await _show_target_card(update, context, target.telegram_id)


async def grant_receive_search(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not _is_admin(update):
        return ConversationHandler.END
    text = (update.message.text if update.message else "") or ""
    if context.user_data is not None:
        context.user_data["grant_search"] = text.strip()
    return await _render_search(update, context, page=0)


async def grant_search_page(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not _is_admin(update):
        return ConversationHandler.END
    query = update.callback_query
    page = int((query.data or "0").rsplit("_", 1)[-1]) if query and query.data else 0
    return await _render_search(update, context, page=page)


async def _render_search(update: Update, context: ContextTypes.DEFAULT_TYPE, page: int) -> int:
    _ = _tr(update)
    raw = ""
    if context.user_data is not None:
        raw = str(context.user_data.get("grant_search") or "")
    found = search_users(raw, page=page, page_size=SEARCH_PAGE_SIZE)
    if found.total == 0:
        await _show(
            update,
            _("No users matched <b>{query}</b>.").format(query=_esc(raw)),
            _kb(
                [
                    [InlineKeyboardButton(_("🔎 Search again"), callback_data="admin_gsearch")],
                    [InlineKeyboardButton(_("❌ Cancel"), callback_data="admin_grant_cancel")],
                ]
            ),
        )
        return GRANT_RESULTS
    rows: list[list[InlineKeyboardButton]] = []
    for item in found.items:
        label = item.first_name or str(item.telegram_id)
        if item.username:
            label = f"{label} (@{item.username})"
        if len(label) > 40:
            label = label[:37] + "..."
        rows.append([InlineKeyboardButton(label, callback_data=f"admin_gu_{item.telegram_id}")])
    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️", callback_data=f"admin_gsp_{page - 1}"))
    if (page + 1) * found.page_size < found.total:
        nav.append(InlineKeyboardButton("➡️", callback_data=f"admin_gsp_{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton(_("🔎 Search again"), callback_data="admin_gsearch")])
    rows.append([InlineKeyboardButton(_("❌ Cancel"), callback_data="admin_grant_cancel")])
    await _show(
        update,
        _("Search results for <b>{query}</b> (page {page}):").format(
            query=_esc(raw), page=page + 1
        ),
        _kb(rows),
    )
    return GRANT_RESULTS


async def grant_pick_user(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not _is_admin(update):
        return ConversationHandler.END
    query = update.callback_query
    telegram_id = int((query.data or "0").rsplit("_", 1)[-1]) if query and query.data else 0
    return await _show_target_card(update, context, telegram_id)


async def _show_target_card(
    update: Update, context: ContextTypes.DEFAULT_TYPE, telegram_id: int
) -> int:
    _ = _tr(update)
    target = load_grant_target(telegram_id)
    if target.status == ERR_DB or target.telegram_id is None:
        await _show(update, _("An error occurred. Please try again."))
        return ConversationHandler.END
    if context.user_data is not None:
        context.user_data["grant_target"] = target.telegram_id
    lines = ["<b>" + _("User") + "</b>", f"<code>{target.telegram_id}</code>"]
    if target.status == "absent":
        lines.append(
            _(
                "This Telegram ID is not in the database. "
                "You can still grant a plan: a user record will be created. "
                "They will not get a message until they press Start."
            )
        )
    else:
        lines.append(_esc(format_person(target.telegram_id, target.first_name, target.username)))
        if target.subscription is None:
            lines.append(_("Subscription: none"))
        elif target.subscription.is_current:
            lines.append(
                _("Subscription: <b>{plan}</b> until <b>{expiry}</b>").format(
                    plan=_esc(target.subscription.plan_name),
                    expiry=_esc(_pretty(target.subscription.end_date)),
                )
            )
        else:
            lines.append(
                _("Subscription: <b>{plan}</b> expired <b>{expiry}</b>").format(
                    plan=_esc(target.subscription.plan_name),
                    expiry=_esc(_pretty(target.subscription.end_date)),
                )
            )
    rows = [[InlineKeyboardButton(_("🎁 Grant a plan"), callback_data="admin_gplans")]]
    if target.subscription is not None:
        rows.append(
            [
                InlineKeyboardButton(
                    _("🛑 End subscription"),
                    callback_data=f"admin_revoke_ask_{target.telegram_id}",
                )
            ]
        )
    rows.append([InlineKeyboardButton(_("❌ Cancel"), callback_data="admin_grant_cancel")])
    await _show(update, "\n".join(lines), _kb(rows))
    return GRANT_USER


async def grant_show_plans(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not _is_admin(update):
        return ConversationHandler.END
    _ = _tr(update)
    query = update.callback_query
    if query is not None and query.data and query.data.startswith("admin_grant_start_"):
        telegram_id = int(query.data.rsplit("_", 1)[-1])
        if context.user_data is not None:
            context.user_data["grant_target"] = telegram_id
    target_id = None if context.user_data is None else context.user_data.get("grant_target")
    if not isinstance(target_id, int):
        await _show(update, _("This step has expired. Open /admin and start again."))
        return ConversationHandler.END
    rows = _plan_rows("admin_gpl_", "admin_grant_cancel", _)
    if rows is None:
        await _show(update, _("There are no active plans to grant. Please create one first."))
        return ConversationHandler.END
    await _show(
        update,
        _("Choose a plan for <code>{user_id}</code>:").format(user_id=target_id),
        _kb(rows),
    )
    return GRANT_PLAN


async def grant_receive_plan(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not _is_admin(update):
        return ConversationHandler.END
    _ = _tr(update)
    query = update.callback_query
    plan_id = int((query.data or "0").rsplit("_", 1)[-1]) if query and query.data else 0
    plan = get_plan_by_id(plan_id)
    if plan is None:
        await _show(update, _("Error: Plan not found."))
        return ConversationHandler.END
    if context.user_data is not None:
        context.user_data["grant_plan_id"] = plan_id
        context.user_data["grant_default_days"] = int(plan["duration_days"])
    await _show(
        update,
        _("Choose how long <b>{plan}</b> lasts:").format(plan=_esc(plan["name"])),
        _kb(_duration_rows("admin_gdy_", int(plan["duration_days"]), "admin_grant_cancel", _)),
    )
    return GRANT_DAYS


async def grant_receive_days(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not _is_admin(update):
        return ConversationHandler.END
    _ = _tr(update)
    query = update.callback_query
    token = (query.data or "").rsplit("_", 1)[-1] if query and query.data else ""
    if token == "custom":
        await _show(
            update,
            _("Send the number of days (1–{max_days}).\n\nSend /cancel to stop.").format(
                max_days=3650
            ),
        )
        return GRANT_DAYS
    days = _days_from_token(token, context)
    if days is None:
        await _show(update, _("That is not a valid number of days."))
        return GRANT_DAYS
    return await _after_days(update, context, days)


async def grant_receive_custom_days(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not _is_admin(update):
        return ConversationHandler.END
    _ = _tr(update)
    text = (update.message.text if update.message else "") or ""
    days = parse_duration_days(text)
    message = update.message
    if message is None:
        return GRANT_DAYS
    if days is None:
        await message.reply_text(_("That is not a valid number of days."))
        return GRANT_DAYS
    return await _after_days(update, context, days)


def _days_from_token(token: str, context: ContextTypes.DEFAULT_TYPE) -> int | None:
    if token == "def":
        stored = None if context.user_data is None else context.user_data.get("grant_default_days")
        if isinstance(stored, int) and stored > 0:
            return stored
        return None
    return parse_duration_days(token)


async def _after_days(update: Update, context: ContextTypes.DEFAULT_TYPE, days: int) -> int:
    _ = _tr(update)
    data = context.user_data or {}
    target_id = data.get("grant_target")
    plan_id = data.get("grant_plan_id")
    if not isinstance(target_id, int) or not isinstance(plan_id, int):
        await _show(update, _("This step has expired. Open /admin and start again."))
        return ConversationHandler.END
    if context.user_data is not None:
        context.user_data["grant_days"] = days
    target = load_grant_target(target_id)
    plan = get_plan_by_id(plan_id)
    plan_name = plan["name"] if plan else str(plan_id)
    if target.subscription is not None and target.subscription.is_current:
        await _show(
            update,
            _(
                "This user already has <b>{plan}</b> until <b>{expiry}</b>.\n\n"
                "Extend adds {days} days to that expiry and switches the plan to "
                "<b>{new_plan}</b>.\n"
                "Replace ends the current subscription and starts <b>{new_plan}</b> "
                "from today for {days} days."
            ).format(
                plan=_esc(target.subscription.plan_name),
                expiry=_esc(_pretty(target.subscription.end_date)),
                days=days,
                new_plan=_esc(plan_name),
            ),
            _kb(
                [
                    [
                        InlineKeyboardButton(
                            _("➕ Extend current subscription"),
                            callback_data="admin_gmd_extend",
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            _("🔁 Replace subscription"),
                            callback_data="admin_gmd_replace",
                        )
                    ],
                    [InlineKeyboardButton(_("❌ Cancel"), callback_data="admin_grant_cancel")],
                ]
            ),
        )
        return GRANT_MODE
    return await _finish_grant(update, context, mode="grant")


async def grant_receive_mode(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not _is_admin(update):
        return ConversationHandler.END
    query = update.callback_query
    token = (query.data or "").rsplit("_", 1)[-1] if query and query.data else ""
    if token not in {"extend", "replace"}:
        return GRANT_MODE
    return await _finish_grant(update, context, mode=token)


async def _finish_grant(update: Update, context: ContextTypes.DEFAULT_TYPE, mode: str) -> int:
    _ = _tr(update)
    data = context.user_data or {}
    target_id = data.get("grant_target")
    plan_id = data.get("grant_plan_id")
    days = data.get("grant_days")
    admin = update.effective_user
    if (
        not isinstance(target_id, int)
        or not isinstance(plan_id, int)
        or not isinstance(days, int)
        or admin is None
    ):
        await _show(update, _("This step has expired. Open /admin and start again."))
        _clear(context, _GRANT_KEYS)
        return ConversationHandler.END
    grant_mode: GrantMode = "grant"
    if mode == "extend":
        grant_mode = "extend"
    elif mode == "replace":
        grant_mode = "replace"
    outcome = apply_plan_grant(
        telegram_id=target_id,
        plan_id=plan_id,
        duration_days=days,
        mode=grant_mode,
        admin_telegram_id=admin.id,
        create_user=True,
    )
    if not outcome.ok and outcome.error == ERR_CHOOSE_MODE:
        if context.user_data is not None:
            context.user_data["grant_days"] = days
        target = load_grant_target(target_id)
        if target.subscription is not None and target.subscription.is_current:
            return await _after_days(update, context, days)
    if not outcome.ok or outcome.end_date is None or outcome.plan_name is None:
        await _show(update, _("An error occurred. Please try again."))
        _clear(context, _GRANT_KEYS)
        return ConversationHandler.END
    user_lang = get_translation_func_for_user(target_id)
    notified = await _notify(
        context,
        target_id,
        grant_notice(outcome.plan_name, _pretty(outcome.end_date), user_lang),
    )
    lines = [
        _("✅ <b>{plan}</b> for <code>{user_id}</code> is active until <b>{expiry}</b>.").format(
            plan=_esc(outcome.plan_name),
            user_id=target_id,
            expiry=_esc(_pretty(outcome.end_date)),
        )
    ]
    if outcome.user_created:
        lines.append(
            _(
                "This Telegram ID was not in the database. "
                "A user record was created and the plan was saved."
            )
        )
    if not notified:
        lines.append(
            _(
                "The plan is saved, but the bot could not message the user. "
                "They have not started the bot, or they blocked it."
            )
        )
    _clear(context, _GRANT_KEYS)
    await _show(
        update,
        "\n\n".join(lines),
        _kb(
            [
                [InlineKeyboardButton(_("🎁 Grant another"), callback_data="admin_grant_open")],
                [InlineKeyboardButton(_("🔙 Back"), callback_data="admin_menu_main")],
            ]
        ),
    )
    return ConversationHandler.END


async def _notify(context: ContextTypes.DEFAULT_TYPE, telegram_id: int, text: str) -> bool:
    try:
        await context.bot.send_message(telegram_id, text, parse_mode=ParseMode.HTML)
    except Exception:
        log.info("Could not deliver a plan notice to %s", telegram_id)
        return False
    return True


async def grant_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not _is_admin(update):
        return ConversationHandler.END
    _clear(context, _GRANT_KEYS)
    _ = _tr(update)
    if update.callback_query is not None:
        await update.callback_query.answer()
    if update.message is not None:
        await update.message.reply_text(_("Operation cancelled."))
    await _admin_panel(update, context)
    return ConversationHandler.END


async def revoke_ask(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_admin(update):
        if update.callback_query is not None:
            await update.callback_query.answer()
        return
    _ = _tr(update)
    query = update.callback_query
    telegram_id = int((query.data or "0").rsplit("_", 1)[-1]) if query and query.data else 0
    target = load_grant_target(telegram_id)
    if target.subscription is None:
        await _show(
            update,
            _("This user has no subscription to end."),
            _kb([[InlineKeyboardButton(_("🔙 Back"), callback_data="admin_menu_users")]]),
        )
        return
    await _show(
        update,
        _(
            "End <b>{plan}</b> for <code>{user_id}</code>?\n"
            "It is currently valid until <b>{expiry}</b>."
        ).format(
            plan=_esc(target.subscription.plan_name),
            user_id=telegram_id,
            expiry=_esc(_pretty(target.subscription.end_date)),
        ),
        _kb(
            [
                [
                    InlineKeyboardButton(
                        _("🛑 End subscription"),
                        callback_data=f"admin_revoke_yes_{telegram_id}",
                    )
                ],
                [
                    InlineKeyboardButton(
                        _("❌ Cancel"),
                        callback_data=f"admin_user_view_{telegram_id}",
                    )
                ],
            ]
        ),
    )


async def revoke_yes(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_admin(update):
        if update.callback_query is not None:
            await update.callback_query.answer()
        return
    _ = _tr(update)
    query = update.callback_query
    admin = update.effective_user
    telegram_id = int((query.data or "0").rsplit("_", 1)[-1]) if query and query.data else 0
    if admin is None:
        return
    outcome = revoke_subscription(telegram_id=telegram_id, admin_telegram_id=admin.id)
    if not outcome.ok:
        message = (
            _("This user has no subscription to end.")
            if outcome.error == ERR_NO_SUBSCRIPTION
            else _("An error occurred. Please try again.")
        )
        await _show(update, message)
        return
    user_text = get_translation_func_for_user(telegram_id)(
        "Your subscription was ended by an admin."
    )
    notified = await _notify(context, telegram_id, user_text)
    lines = [_("The subscription was ended.")]
    if not notified:
        lines.append(
            _("The user could not be notified. They have not started the bot, or they blocked it.")
        )
    await _show(
        update,
        "\n\n".join(lines),
        _kb([[InlineKeyboardButton(_("🔙 Back"), callback_data="admin_menu_users")]]),
    )


def _code_menu_keyboard(_) -> InlineKeyboardMarkup:
    return _kb(
        [
            [InlineKeyboardButton(_("➕ Generate links"), callback_data="admin_code_new")],
            [
                InlineKeyboardButton(_("🟢 Unused"), callback_data="admin_code_list_unused_0"),
                InlineKeyboardButton(_("🔵 Used"), callback_data="admin_code_list_used_0"),
            ],
            [
                InlineKeyboardButton(_("⌛ Expired"), callback_data="admin_code_list_expired_0"),
                InlineKeyboardButton(_("🚫 Revoked"), callback_data="admin_code_list_revoked_0"),
            ],
            [InlineKeyboardButton(_("🔙 Back"), callback_data="admin_menu_main")],
        ]
    )


def _code_menu_text(_) -> str:
    return _(
        "<b>Activation links</b>\n\n"
        "Each link works once. Opening it activates the plan for that user."
    )


async def codes_open(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not _is_admin(update):
        return ConversationHandler.END
    _clear(context, _CODE_KEYS)
    _ = _tr(update)
    await _show(update, _code_menu_text(_), _code_menu_keyboard(_))
    return CODE_MENU


async def codes_show_plans(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not _is_admin(update):
        return ConversationHandler.END
    _ = _tr(update)
    rows = _plan_rows("admin_cpl_", "admin_codes_cancel", _)
    if rows is None:
        await _show(update, _("There are no active plans to grant. Please create one first."))
        return CODE_MENU
    await _show(update, _("Choose a plan for the activation links:"), _kb(rows))
    return CODE_PLAN


async def codes_receive_plan(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not _is_admin(update):
        return ConversationHandler.END
    _ = _tr(update)
    query = update.callback_query
    plan_id = int((query.data or "0").rsplit("_", 1)[-1]) if query and query.data else 0
    plan = get_plan_by_id(plan_id)
    if plan is None:
        await _show(update, _("Error: Plan not found."))
        return CODE_MENU
    if context.user_data is not None:
        context.user_data["code_plan_id"] = plan_id
        context.user_data["code_default_days"] = int(plan["duration_days"])
    await _show(
        update,
        _("Choose how long <b>{plan}</b> lasts:").format(plan=_esc(plan["name"])),
        _kb(_duration_rows("admin_cdy_", int(plan["duration_days"]), "admin_codes_cancel", _)),
    )
    return CODE_DAYS


async def codes_receive_days(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not _is_admin(update):
        return ConversationHandler.END
    _ = _tr(update)
    query = update.callback_query
    token = (query.data or "").rsplit("_", 1)[-1] if query and query.data else ""
    if token == "custom":
        await _show(
            update,
            _("Send the number of days (1–{max_days}).\n\nSend /cancel to stop.").format(
                max_days=3650
            ),
        )
        return CODE_DAYS
    days = _code_days_from_token(token, context)
    if days is None:
        await _show(update, _("That is not a valid number of days."))
        return CODE_DAYS
    return await _ask_count(update, context, days)


async def codes_receive_custom_days(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not _is_admin(update):
        return ConversationHandler.END
    _ = _tr(update)
    days = parse_duration_days((update.message.text if update.message else "") or "")
    message = update.message
    if message is None:
        return CODE_DAYS
    if days is None:
        await message.reply_text(_("That is not a valid number of days."))
        return CODE_DAYS
    return await _ask_count(update, context, days)


def _code_days_from_token(token: str, context: ContextTypes.DEFAULT_TYPE) -> int | None:
    if token == "def":
        stored = None if context.user_data is None else context.user_data.get("code_default_days")
        if isinstance(stored, int) and stored > 0:
            return stored
        return None
    return parse_duration_days(token)


async def _ask_count(update: Update, context: ContextTypes.DEFAULT_TYPE, days: int) -> int:
    _ = _tr(update)
    if context.user_data is not None:
        context.user_data["code_days"] = days
    await _show(
        update,
        _("How many links? (1–50)"),
        _kb(
            [
                [
                    InlineKeyboardButton("1", callback_data="admin_ccn_1"),
                    InlineKeyboardButton("5", callback_data="admin_ccn_5"),
                    InlineKeyboardButton("10", callback_data="admin_ccn_10"),
                ],
                [
                    InlineKeyboardButton("25", callback_data="admin_ccn_25"),
                    InlineKeyboardButton("50", callback_data="admin_ccn_50"),
                ],
                [InlineKeyboardButton(_("✏️ Custom count"), callback_data="admin_ccn_custom")],
                [InlineKeyboardButton(_("❌ Cancel"), callback_data="admin_codes_cancel")],
            ]
        ),
    )
    return CODE_COUNT


async def codes_receive_count(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not _is_admin(update):
        return ConversationHandler.END
    _ = _tr(update)
    query = update.callback_query
    token = (query.data or "").rsplit("_", 1)[-1] if query and query.data else ""
    if token == "custom":
        await _show(update, _("Send how many links to create (1–50)."))
        return CODE_COUNT
    count = parse_batch_count(token)
    if count is None:
        await _show(update, _("Send a number from 1 to 50."))
        return CODE_COUNT
    return await _ask_expiry(update, context, count)


async def codes_receive_custom_count(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not _is_admin(update):
        return ConversationHandler.END
    _ = _tr(update)
    count = parse_batch_count((update.message.text if update.message else "") or "")
    message = update.message
    if message is None:
        return CODE_COUNT
    if count is None:
        await message.reply_text(_("Send a number from 1 to 50."))
        return CODE_COUNT
    return await _ask_expiry(update, context, count)


async def _ask_expiry(update: Update, context: ContextTypes.DEFAULT_TYPE, count: int) -> int:
    _ = _tr(update)
    if context.user_data is not None:
        context.user_data["code_count"] = count
    await _show(
        update,
        _("When should unused links expire?"),
        _kb(
            [
                [InlineKeyboardButton(_("No expiry"), callback_data="admin_cex_none")],
                [
                    InlineKeyboardButton(_("1 day"), callback_data="admin_cex_1"),
                    InlineKeyboardButton(_("7 days"), callback_data="admin_cex_7"),
                    InlineKeyboardButton(_("30 days"), callback_data="admin_cex_30"),
                ],
                [InlineKeyboardButton(_("📅 Custom date"), callback_data="admin_cex_custom")],
                [InlineKeyboardButton(_("❌ Cancel"), callback_data="admin_codes_cancel")],
            ]
        ),
    )
    return CODE_EXPIRY


async def codes_receive_expiry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not _is_admin(update):
        return ConversationHandler.END
    _ = _tr(update)
    query = update.callback_query
    token = (query.data or "").rsplit("_", 1)[-1] if query and query.data else ""
    if token == "custom":
        await _show(
            update,
            _("Send a date as YYYY-MM-DD, or a number of days until the link expires."),
        )
        return CODE_EXPIRY
    if token == "none":
        return await _issue(update, context, expires_at=None)
    days = parse_duration_days(token)
    if days is None:
        await _show(update, _("That expiry is not valid."))
        return CODE_EXPIRY
    expires_at = datetime.now(UTC).replace(tzinfo=None) + timedelta(days=days)
    return await _issue(update, context, expires_at=expires_at)


async def codes_receive_custom_expiry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not _is_admin(update):
        return ConversationHandler.END
    _ = _tr(update)
    expiry = parse_link_expiry((update.message.text if update.message else "") or "")
    message = update.message
    if message is None:
        return CODE_EXPIRY
    if expiry is None:
        await message.reply_text(
            _("That expiry is not valid. Use YYYY-MM-DD or a number of days in the future.")
        )
        return CODE_EXPIRY
    return await _issue(update, context, expires_at=expiry)


async def _issue(
    update: Update, context: ContextTypes.DEFAULT_TYPE, expires_at: datetime | None
) -> int:
    _ = _tr(update)
    data = context.user_data or {}
    plan_id = data.get("code_plan_id")
    days = data.get("code_days")
    count = data.get("code_count")
    admin = update.effective_user
    if (
        not isinstance(plan_id, int)
        or not isinstance(days, int)
        or not isinstance(count, int)
        or admin is None
    ):
        await _show(update, _("This step has expired. Open /admin and start again."))
        _clear(context, _CODE_KEYS)
        return ConversationHandler.END
    outcome = issue_activation_codes(
        plan_id=plan_id,
        duration_days=days,
        count=count,
        admin_telegram_id=admin.id,
        expires_at=expires_at,
    )
    if not outcome.ok or not outcome.codes:
        await _show(update, _("An error occurred. Please try again."))
        _clear(context, _CODE_KEYS)
        return ConversationHandler.END
    username = await bot_username(context)
    plan_name = outcome.codes[0].plan_name
    header = _("Created {count} activation links for <b>{plan}</b> ({days} days).").format(
        count=len(outcome.codes),
        plan=_esc(plan_name),
        days=days,
    )
    if expires_at is not None:
        header += "\n" + _("Unused links expire at <b>{expiry}</b>.").format(
            expiry=_esc(_pretty(expires_at.replace(tzinfo=UTC).isoformat()))
        )
    if username:
        links = [build_activation_link(username, item.code) for item in outcome.codes]
    else:
        header += "\n" + _(
            "The bot has no public username, so deep links could not be built. Codes:"
        )
        links = [item.code for item in outcome.codes]
    _clear(context, _CODE_KEYS)
    chunks = _chunks(links, 20)
    first = header + "\n\n" + "\n".join(f"<code>{_esc(link)}</code>" for link in chunks[0])
    await _show(
        update,
        first,
        _kb([[InlineKeyboardButton(_("🔙 Back"), callback_data="admin_codes_open")]]),
    )
    message = update.effective_message
    chat = update.effective_chat
    for extra in chunks[1:]:
        body = "\n".join(f"<code>{_esc(link)}</code>" for link in extra)
        if chat is not None:
            await context.bot.send_message(
                chat.id,
                body,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
            )
        elif message is not None:
            await message.reply_text(body, parse_mode=ParseMode.HTML, disable_web_page_preview=True)
    return ConversationHandler.END


def _chunks(items: list[str], size: int) -> list[list[str]]:
    return [items[index : index + size] for index in range(0, len(items), size)]


async def codes_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not _is_admin(update):
        return ConversationHandler.END
    _clear(context, _CODE_KEYS)
    _ = _tr(update)
    if update.message is not None:
        await update.message.reply_text(_("Operation cancelled."))
    await _admin_panel(update, context)
    return ConversationHandler.END


def _status_label(status: str, _) -> str:
    labels = {
        "unused": _("Unused"),
        "used": _("Used"),
        "expired": _("Expired"),
        "revoked": _("Revoked"),
    }
    return labels.get(status, status)


async def codes_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_admin(update):
        if update.callback_query is not None:
            await update.callback_query.answer()
        return
    _ = _tr(update)
    query = update.callback_query
    parts = (query.data or "").split("_") if query and query.data else []
    # admin_code_list_{status}_{page}
    status = parts[3] if len(parts) >= 5 else "unused"
    page = int(parts[4]) if len(parts) >= 5 and parts[4].isdigit() else 0
    found = list_activation_codes(status, page=page, page_size=CODE_PAGE_SIZE)
    rows: list[list[InlineKeyboardButton]] = []
    for item in found.items:
        suffix = item.code[-6:]
        label = f"{item.plan_name} · {item.duration_days}d · …{suffix}"
        if len(label) > 40:
            label = label[:37] + "..."
        rows.append([InlineKeyboardButton(label, callback_data=f"admin_code_view_{item.id}")])
    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️", callback_data=f"admin_code_list_{status}_{page - 1}"))
    if (page + 1) * found.page_size < found.total:
        nav.append(InlineKeyboardButton("➡️", callback_data=f"admin_code_list_{status}_{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton(_("🔙 Back"), callback_data="admin_codes_open")])
    title = _("{status} activation codes (page {page})").format(
        status=_status_label(status, _),
        page=page + 1,
    )
    if found.total == 0:
        title = _("No activation codes in this list.")
    await _show(update, title, _kb(rows))


async def codes_view(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_admin(update):
        if update.callback_query is not None:
            await update.callback_query.answer()
        return
    _ = _tr(update)
    query = update.callback_query
    code_id = int((query.data or "0").rsplit("_", 1)[-1]) if query and query.data else 0
    detail = get_activation_code(code_id)
    if detail is None:
        await _show(update, _("This activation code is not valid."))
        return
    username = await bot_username(context)
    link = build_activation_link(username, detail.code) if username else detail.code
    if detail.redeemed_telegram_id is None:
        who = "—"
    else:
        who = format_person(
            detail.redeemed_telegram_id,
            detail.redeemed_first_name,
            detail.redeemed_username,
        )
    text = _(
        "<b>Activation code</b>\n"
        "<code>{code}</code>\n"
        "<b>Link:</b> <code>{link}</code>\n"
        "<b>Plan:</b> {plan}\n"
        "<b>Days:</b> {days}\n"
        "<b>Status:</b> {status}\n"
        "<b>Created by:</b> <code>{admin_id}</code>\n"
        "<b>Created:</b> {created}\n"
        "<b>Expires:</b> {expires}\n"
        "<b>Redeemed by:</b> {who}\n"
        "<b>Redeemed:</b> {when}"
    ).format(
        code=_esc(detail.code),
        link=_esc(link),
        plan=_esc(detail.plan_name),
        days=detail.duration_days,
        status=_esc(_status_label(detail.status, _)),
        admin_id=detail.created_by_telegram_id,
        created=_esc(_pretty(detail.created_at)),
        expires=_esc(_pretty(detail.expires_at)),
        who=_esc(who),
        when=_esc(_pretty(detail.used_at)),
    )
    rows: list[list[InlineKeyboardButton]] = []
    if detail.status in {"unused", "expired"}:
        rows.append(
            [
                InlineKeyboardButton(
                    _("🚫 Revoke code"),
                    callback_data=f"admin_code_revoke_ask_{detail.id}",
                )
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(
                _("🔙 Back"),
                callback_data=f"admin_code_list_{detail.status}_0",
            )
        ]
    )
    await _show(update, text, _kb(rows))


async def codes_revoke_ask(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_admin(update):
        if update.callback_query is not None:
            await update.callback_query.answer()
        return
    _ = _tr(update)
    query = update.callback_query
    code_id = int((query.data or "0").rsplit("_", 1)[-1]) if query and query.data else 0
    await _show(
        update,
        _("Revoke this unused code? It will stop working immediately."),
        _kb(
            [
                [
                    InlineKeyboardButton(
                        _("🚫 Revoke code"),
                        callback_data=f"admin_code_revoke_yes_{code_id}",
                    )
                ],
                [
                    InlineKeyboardButton(
                        _("❌ Cancel"),
                        callback_data=f"admin_code_view_{code_id}",
                    )
                ],
            ]
        ),
    )


async def codes_revoke_yes(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_admin(update):
        if update.callback_query is not None:
            await update.callback_query.answer()
        return
    _ = _tr(update)
    query = update.callback_query
    admin = update.effective_user
    code_id = int((query.data or "0").rsplit("_", 1)[-1]) if query and query.data else 0
    if admin is None:
        return
    result = revoke_activation_code(code_id=code_id, admin_telegram_id=admin.id)
    if result == "ok":
        text = _("The activation code was revoked.")
    elif result == ERR_NOT_REVOCABLE:
        text = _("This code can no longer be revoked.")
    else:
        text = _("An error occurred. Please try again.")
    await _show(
        update,
        text,
        _kb([[InlineKeyboardButton(_("🔙 Back"), callback_data="admin_codes_open")]]),
    )


async def stale_step(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_admin(update):
        if update.callback_query is not None:
            await update.callback_query.answer()
        return
    _ = _tr(update)
    await _show(
        update,
        _("This step has expired. Open /admin and start again."),
        _kb([[InlineKeyboardButton(_("🔙 Back"), callback_data="admin_menu_main")]]),
    )


async def _codes_list_state(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await codes_list(update, context)
    return CODE_MENU


async def _codes_view_state(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await codes_view(update, context)
    return CODE_MENU


async def _codes_revoke_ask_state(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await codes_revoke_ask(update, context)
    return CODE_MENU


async def _codes_revoke_yes_state(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await codes_revoke_yes(update, context)
    return CODE_MENU


_ADMIN_TEXT = filters.TEXT & ~filters.COMMAND & filters.User(user_id=config.ADMIN_IDS)

grant_conv_handler = ConversationHandler(
    entry_points=[
        CallbackQueryHandler(grant_open, pattern=r"^admin_grant_open$"),
        CallbackQueryHandler(grant_show_plans, pattern=r"^admin_grant_start_\d+$"),
    ],
    states={
        GRANT_MENU: [
            CallbackQueryHandler(grant_ask_lookup, pattern=r"^admin_glookup$"),
            CallbackQueryHandler(grant_ask_search, pattern=r"^admin_gsearch$"),
        ],
        GRANT_LOOKUP: [MessageHandler(_ADMIN_TEXT, grant_receive_lookup)],
        GRANT_SEARCH: [MessageHandler(_ADMIN_TEXT, grant_receive_search)],
        GRANT_RESULTS: [
            CallbackQueryHandler(grant_search_page, pattern=r"^admin_gsp_\d+$"),
            CallbackQueryHandler(grant_pick_user, pattern=r"^admin_gu_\d+$"),
            CallbackQueryHandler(grant_ask_search, pattern=r"^admin_gsearch$"),
        ],
        GRANT_USER: [
            CallbackQueryHandler(grant_show_plans, pattern=r"^admin_gplans$"),
        ],
        GRANT_PLAN: [
            CallbackQueryHandler(grant_receive_plan, pattern=r"^admin_gpl_\d+$"),
        ],
        GRANT_DAYS: [
            CallbackQueryHandler(grant_receive_days, pattern=r"^admin_gdy_"),
            MessageHandler(_ADMIN_TEXT, grant_receive_custom_days),
        ],
        GRANT_MODE: [
            CallbackQueryHandler(grant_receive_mode, pattern=r"^admin_gmd_(extend|replace)$"),
        ],
    },
    fallbacks=[
        CommandHandler("cancel", grant_cancel, filters=filters.User(user_id=config.ADMIN_IDS)),
        CallbackQueryHandler(grant_cancel, pattern=r"^admin_grant_cancel$"),
    ],
    allow_reentry=True,
    per_message=False,
    name="admin_grant",
)

_CODE_LIST_PATTERN = r"^admin_code_list_(unused|used|expired|revoked)_\d+$"
_CODE_LIST_HANDLERS = [
    CallbackQueryHandler(codes_list, pattern=_CODE_LIST_PATTERN),
    CallbackQueryHandler(codes_view, pattern=r"^admin_code_view_\d+$"),
    CallbackQueryHandler(codes_revoke_ask, pattern=r"^admin_code_revoke_ask_\d+$"),
    CallbackQueryHandler(codes_revoke_yes, pattern=r"^admin_code_revoke_yes_\d+$"),
]

codes_conv_handler = ConversationHandler(
    entry_points=[CallbackQueryHandler(codes_open, pattern=r"^admin_codes_open$")],
    states={
        CODE_MENU: [
            CallbackQueryHandler(codes_show_plans, pattern=r"^admin_code_new$"),
            CallbackQueryHandler(_codes_list_state, pattern=_CODE_LIST_PATTERN),
            CallbackQueryHandler(_codes_view_state, pattern=r"^admin_code_view_\d+$"),
            CallbackQueryHandler(_codes_revoke_ask_state, pattern=r"^admin_code_revoke_ask_\d+$"),
            CallbackQueryHandler(_codes_revoke_yes_state, pattern=r"^admin_code_revoke_yes_\d+$"),
        ],
        CODE_PLAN: [CallbackQueryHandler(codes_receive_plan, pattern=r"^admin_cpl_\d+$")],
        CODE_DAYS: [
            CallbackQueryHandler(codes_receive_days, pattern=r"^admin_cdy_"),
            MessageHandler(_ADMIN_TEXT, codes_receive_custom_days),
        ],
        CODE_COUNT: [
            CallbackQueryHandler(codes_receive_count, pattern=r"^admin_ccn_"),
            MessageHandler(_ADMIN_TEXT, codes_receive_custom_count),
        ],
        CODE_EXPIRY: [
            CallbackQueryHandler(codes_receive_expiry, pattern=r"^admin_cex_"),
            MessageHandler(_ADMIN_TEXT, codes_receive_custom_expiry),
        ],
    },
    fallbacks=[
        CommandHandler("cancel", codes_cancel, filters=filters.User(user_id=config.ADMIN_IDS)),
        CallbackQueryHandler(codes_cancel, pattern=r"^admin_codes_cancel$"),
        CallbackQueryHandler(_codes_list_state, pattern=_CODE_LIST_PATTERN),
        CallbackQueryHandler(_codes_view_state, pattern=r"^admin_code_view_\d+$"),
        CallbackQueryHandler(_codes_revoke_ask_state, pattern=r"^admin_code_revoke_ask_\d+$"),
        CallbackQueryHandler(_codes_revoke_yes_state, pattern=r"^admin_code_revoke_yes_\d+$"),
    ],
    allow_reentry=True,
    per_message=False,
    name="admin_codes",
)

grant_admin_handlers: list[BaseHandler[Any, Any, Any]] = [
    grant_conv_handler,
    codes_conv_handler,
    CallbackQueryHandler(revoke_ask, pattern=r"^admin_revoke_ask_\d+$"),
    CallbackQueryHandler(revoke_yes, pattern=r"^admin_revoke_yes_\d+$"),
    *_CODE_LIST_HANDLERS,
    CallbackQueryHandler(
        stale_step,
        pattern=r"^admin_g(lookup|search|plans|sp_\d+|u_\d+|pl_\d+|dy_.+|md_(extend|replace))$",
    ),
    CallbackQueryHandler(
        stale_step,
        pattern=r"^admin_c(pl_\d+|dy_.+|cn_.+|ex_.+)$",
    ),
]
