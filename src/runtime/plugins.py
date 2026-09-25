"""Plugin types, registry, and command dispatch.

A plugin is a small object with metadata and optional ``handle`` / ``spawn``
methods. Command plugins answer outgoing messages from the account itself.
Background plugins return a long-running coroutine from ``spawn``. One plugin
raising does not cancel the others.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable
from dataclasses import dataclass
from typing import Any

from src.config import get_settings
from src.runtime.flood import AccountLimiter
from src.runtime.store import get_plugin_setting

log = logging.getLogger(__name__)

GROUPS_PLUGIN = "groups"
CODEMON_PLUGIN = "codemon"
CORE_PLUGIN = "core"


@dataclass(frozen=True)
class BotCommand:
    name: str
    description_en: str
    description_ar: str

    def description(self, language: str) -> str:
        if language == "ar":
            return self.description_ar
        return self.description_en


@dataclass(frozen=True)
class PluginMeta:
    name: str
    description_en: str
    description_ar: str
    commands: tuple[BotCommand, ...] = ()
    default_enabled: bool = True

    def description(self, language: str) -> str:
        if language == "ar":
            return self.description_ar
        return self.description_en


class PluginSettings:
    """JSON settings stored for one account and one plugin."""

    def __init__(self, account_id: int, plugin_name: str) -> None:
        self.account_id = account_id
        self.plugin_name = plugin_name

    def get(self, key: str, default: Any = None) -> Any:
        return get_plugin_setting(self.account_id, self.plugin_name, key, default)

    def set(self, key: str, value: Any) -> None:
        from src.runtime.store import set_plugin_setting

        set_plugin_setting(self.account_id, self.plugin_name, key, value)

    def delete(self, key: str) -> None:
        from src.runtime.store import delete_plugin_setting

        delete_plugin_setting(self.account_id, self.plugin_name, key)


class AccountSession:
    """Handle passed to background plugins for the life of one connection."""

    def __init__(
        self,
        *,
        account_id: int,
        client: Any,
        details: dict[str, Any],
        limiter: AccountLimiter,
        stop_event: Any,
        refresh: Any,
    ) -> None:
        self.account_id = account_id
        self.client = client
        self.details = details
        self.limiter = limiter
        self.stop_event = stop_event
        self.refresh = refresh

    def stopped(self) -> bool:
        return bool(self.stop_event.is_set())

    def settings_for(self, plugin_name: str) -> PluginSettings:
        return PluginSettings(self.account_id, plugin_name)

    async def sleep(self, seconds: float) -> None:
        import asyncio

        if seconds <= 0 or self.stopped():
            return
        stop_task = asyncio.create_task(self.stop_event.wait())
        refresh_task = asyncio.create_task(self.refresh.wait())
        try:
            await asyncio.wait(
                {stop_task, refresh_task},
                timeout=seconds,
                return_when=asyncio.FIRST_COMPLETED,
            )
        finally:
            for task in (stop_task, refresh_task):
                if not task.done():
                    task.cancel()
            self.refresh.clear()


class CommandContext:
    """One outgoing command invocation."""

    def __init__(
        self,
        *,
        client: Any,
        account_id: int,
        message: Any,
        command: str,
        args: str,
        prefix: str,
        language: str,
        plugin_name: str,
        limiter: AccountLimiter,
    ) -> None:
        self.client = client
        self.account_id = account_id
        self.message = message
        self.command = command
        self.args = args
        self.prefix = prefix
        self.language = language
        self.plugin_name = plugin_name
        self.limiter = limiter
        self.settings = PluginSettings(account_id, plugin_name)

    async def reply(self, text: str) -> Any:
        chat = getattr(self.message, "chat", None)
        chat_id = getattr(chat, "id", None)
        if chat_id is None:
            chat_id = "me"
        message_id = getattr(self.message, "id", None)

        async def _send() -> Any:
            kwargs: dict[str, Any] = {}
            if message_id is not None:
                kwargs["reply_to_message_id"] = message_id
            return await self.client.send_message(chat_id, text, **kwargs)

        return await self.limiter.run(_send)


class Plugin:
    """Base class. Subclasses replace ``meta`` and the methods they need."""

    meta: PluginMeta

    async def handle(self, ctx: CommandContext) -> None:
        return None

    def spawn(self, session: AccountSession) -> Awaitable[None] | None:
        return None


_REGISTRY: dict[str, Plugin] = {}
_LOADED = False


def register(plugin: Plugin) -> None:
    name = plugin.meta.name
    current = _REGISTRY.get(name)
    if current is not None and current is not plugin:
        log.warning("Replacing plugin registration for %s", name)
    _REGISTRY[name] = plugin


def load_plugins() -> None:
    global _LOADED
    if _LOADED:
        return
    from src.plugins.codewatch import plugin as codewatch
    from src.plugins.groups import plugin as groups
    from src.plugins.help import plugin as help_plugin
    from src.plugins.identify import plugin as identify
    from src.plugins.ping import plugin as ping

    for plugin in (groups, codewatch, ping, identify, help_plugin):
        register(plugin)
    _LOADED = True


def all_plugins() -> list[Plugin]:
    load_plugins()
    return list(_REGISTRY.values())


def get_plugin(name: str) -> Plugin | None:
    load_plugins()
    return _REGISTRY.get(name)


def command_prefix(account_id: int) -> str:
    raw = get_plugin_setting(account_id, CORE_PLUGIN, "prefix")
    if isinstance(raw, str) and raw and not any(character.isspace() for character in raw):
        return raw
    return get_settings().userbot_prefix


def command_map(plugins: list[Plugin] | None = None) -> dict[str, tuple[Plugin, BotCommand]]:
    source = all_plugins() if plugins is None else plugins
    found: dict[str, tuple[Plugin, BotCommand]] = {}
    for plugin in source:
        for command in plugin.meta.commands:
            found[command.name] = (plugin, command)
            found.setdefault(command.name.casefold(), (plugin, command))
    return found


def _message_text(message: Any) -> str:
    text = getattr(message, "text", None) or getattr(message, "caption", None) or ""
    return str(text)


def is_self_outgoing(message: Any) -> bool:
    """True when the managed account sent the message."""
    if getattr(message, "outgoing", False):
        return True
    sender = getattr(message, "from_user", None)
    return bool(sender and getattr(sender, "is_self", False))


def parse_command(text: str, prefix: str) -> tuple[str, str] | None:
    if not prefix or not text.startswith(prefix):
        return None
    body = text[len(prefix) :].strip()
    if not body:
        return None
    name, _, args = body.partition(" ")
    if not name:
        return None
    return name, args.strip()


class Dispatcher:
    """Route one outgoing message to at most one plugin."""

    def __init__(self, plugins: list[Plugin] | None = None) -> None:
        self.plugins = plugins

    async def handle_message(
        self,
        *,
        account_id: int,
        client: Any,
        message: Any,
        limiter: AccountLimiter,
        language: str = "en",
    ) -> bool:
        if not is_self_outgoing(message):
            return False
        parsed = parse_command(_message_text(message), command_prefix(account_id))
        if parsed is None:
            return False
        name, args = parsed
        match = command_map(self.plugins).get(name) or command_map(self.plugins).get(
            name.casefold()
        )
        if match is None:
            return False
        plugin, _command = match
        from src.runtime.gating import plugin_is_enabled

        if not plugin_is_enabled(account_id, plugin):
            return False
        ctx = CommandContext(
            client=client,
            account_id=account_id,
            message=message,
            command=name,
            args=args,
            prefix=command_prefix(account_id),
            language=language,
            plugin_name=plugin.meta.name,
            limiter=limiter,
        )
        try:
            await plugin.handle(ctx)
        except Exception:
            log.exception(
                "Plugin %s failed on account %s command %s",
                plugin.meta.name,
                account_id,
                name,
            )
        return True
