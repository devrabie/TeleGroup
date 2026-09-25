# Writing a TeleGroup plugin

Plugins run inside the account runtime, on the Kurigram client for one managed account. The control bot does not execute userbot commands. It only toggles plugins and plan allowlists.

A plugin is a subclass of `src.runtime.plugins.Plugin` with a `meta` object. Put the module in `src/plugins/` and register it from `load_plugins()` in `src/runtime/plugins.py`.

## Metadata

```python
from src.runtime.plugins import BotCommand, CommandContext, Plugin, PluginMeta


class PingPlugin(Plugin):
    meta = PluginMeta(
        name="ping",  # short, no underscores (used in callback data)
        description_en="Reply with this account's latency.",
        description_ar="الرد بزمن استجابة هذا الحساب.",
        commands=(
            BotCommand("ping", "Reply with this account's latency.", "الرد بزمن استجابة هذا الحساب."),
            BotCommand("فحص", "Reply with this account's latency.", "الرد بزمن استجابة هذا الحساب."),
        ),
        default_enabled=True,
    )

    async def handle(self, ctx: CommandContext) -> None:
        await ctx.reply("pong")


plugin = PingPlugin()
```

- `name` is the stable id stored in `plan_plugins` and `account_plugins`.
- `commands` are matched without the prefix. Latin names are case-insensitive. Arabic names are matched as written.
- `default_enabled` applies when the account has no row in `account_plugins`. `groups` and `codemon` are special: they follow `managed_accounts.is_active` and `code_monitor_enabled`.
- New plugins are not added to existing plans. `add_plan` grants whatever is registered at creation time. Admins change the allowlist from the plan editor.

## Commands

The dispatcher accepts an outgoing message from the account itself (saved messages or any chat the account sends in). The default prefix is `.` (`USERBOT_PREFIX`). An account can override it with a core setting:

```python
from src.runtime.plugins import CORE_PLUGIN, PluginSettings

PluginSettings(account_id, CORE_PLUGIN).set("prefix", "!")
```

`CommandContext` gives you:

- `ctx.command`, `ctx.args`, `ctx.prefix`, `ctx.language` (`en` or `ar`, from the owner's control-bot language)
- `ctx.client` — the running Kurigram client
- `ctx.reply(text)` — sends through the account's FloodWait limiter
- `ctx.settings` — JSON settings for this plugin and account (`get` / `set` / `delete`)
- `ctx.limiter.run(async_fn)` — any other Telegram call

Reply text should follow `ctx.language`. One plugin exception is logged and swallowed. The client and the other plugins keep running.

## Background work

Return a coroutine from `spawn` to run for as long as the client is connected. Sleep with `session.sleep(seconds)` so shutdown and setting changes wake the task. Check `session.stopped()` in the loop.

```python
def spawn(self, session):
    return self._run(session)

async def _run(self, session):
    while not session.stopped():
        await session.limiter.run(session.client.get_me)
        await session.sleep(60)
```

`groups` and `codemon` are background plugins. Group creation still uses the same title, log row, schedule, and backoff as the old 5-minute job. Code monitoring still forwards the same security messages; it only attaches handlers to the runtime client instead of opening a second session.

## Plan gating

`plugin_is_enabled(account_id, plugin)` is false when:

- the account is deleted or has no active subscription
- the owner's plan has no `plan_plugins` row for this name
- the account toggle is off

The account menu's Plugins screen flips the toggle. The admin plan screen edits the allowlist. Both wake the worker.

## What not to do

Construct Kurigram clients only with `build_user_client` (the runtime already did that). Do not call `client.start()` from a plugin. Do not catch `FloodWait` yourself unless you need custom scheduling; `ctx.limiter` records the wait and retries short waits once.

Phase 3 will add the rest of the userbot command set on top of this registry. Keep each command in its own plugin module rather than growing a single handler file.
