"""Single catch-all router for free text that is not part of a conversation.

python-telegram-bot runs only the first matching handler in a group, so two
catch-all ``MessageHandler(filters.TEXT & ~filters.COMMAND, ...)`` entries in
the same group shadow each other and any ConversationHandler placed after
them. This router is registered once, after every ConversationHandler, and
dispatches to the pending single-message input using the existing
``user_data`` flags.
"""

from __future__ import annotations

from telegram import Update
from telegram.ext import ContextTypes

from src.account_admin_menu import receive_account_admin_text
from src.plugin_menu import receive_plugin_setting

ACCOUNT_ADMIN_ADD_KEY = "account_admin_add"
PLUGIN_SETTING_KEY = "plugin_setting_field"


async def route_pending_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send free text to the input that is waiting for it, or ignore it."""
    user_data = context.user_data
    if not user_data:
        return
    if user_data.get(ACCOUNT_ADMIN_ADD_KEY):
        await receive_account_admin_text(update, context)
        return
    if user_data.get(PLUGIN_SETTING_KEY):
        await receive_plugin_setting(update, context)
