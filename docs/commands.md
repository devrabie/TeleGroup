# Phase 3 commands

Outgoing messages from the managed account, starting with `USERBOT_PREFIX` (default `.`). Arabic is the primary name. English is an alias. `.الاوامر admin` (or `.help admin`) prints one plugin's descriptions.

Plugins are idle until you use them. PM protection, logging, and game notices stay off until you turn them on. Locks and replies do nothing until you add one. Broadcast and gifts ask for a second command before they spend messages or Stars.

Existing plans do not gain these plugins from the migration. Allow them in the plan editor. New plans include them.

| Arabic | English | Description |
| --- | --- | --- |
| حظر | ban | Ban by reply, @username, or id |
| الغاء الحظر | unban | Remove a ban |
| طرد | kick | Kick (ban, then unban) |
| كتم | mute | Stop a user sending |
| الغاء الكتم | unmute | Allow sending again |
| رفع مشرف | promote | Promote to admin |
| تنزيل مشرف | demote | Remove admin rights |
| تثبيت | pin | Pin the replied message |
| الغاء التثبيت | unpin | Unpin the replied message |
| مسح | del | Delete the replied message |
| تنظيف | purge | Delete from the reply up to this command (100 max) |
| تخزين | storage | Show, or `on` / `off` (`تشغيل` / `ايقاف`) |
| وضع التخزين | setlog | Log chat: `here`, `me`, or an id (`هنا`, `محفوظات`) |
| رد عام | gfilter | Global reply: `keyword \| reply` |
| حذف رد عام | ungfilter | Delete a global reply |
| الردود العامة | gfilters | List global replies |
| رد | filter | Reply in this chat: `keyword \| reply` |
| حذف رد | unfilter | Delete this chat's reply |
| الردود | filters | List this chat's replies |
| غائب | afk | Away mode, with an optional message. Turns off on your next normal message |
| الغاء الغياب | unafk | Turn away mode off |
| الحماية | pmpermit | PM protection status, or `on` / `off` |
| سماح | approve | Approve a private sender |
| رفض | disapprove | Remove approval |
| عدد التحذير | pmwarn | Warnings before an automatic block (1–10) |
| قفل | lock | Lock a type in this group |
| فتح | unlock | Unlock a type |
| الاقفال | locks | List locks in this group |
| تاك | tagall | Mention members in batches. Optional text |
| ايقاف التاك | tagstop | Stop mentions |
| اذاعة | broadcast | Prepare a group broadcast. Does not send yet |
| اذاعة خاص | pbroadcast | Prepare a private-chat broadcast |
| تأكيد الاذاعة | confirmbroadcast | Send the prepared broadcast |
| ايقاف الاذاعة | broadcaststop | Stop or discard a broadcast |
| انشاء كروب | creategroup | Create a supergroup. Also `انشاء مجموعة` and `create group` |
| انشاء قناة | createchannel | Create a channel. Also `create channel` |
| نقل ملكية | transfer | Start an ownership transfer. Also `نقل` |
| كلمة السر | cloudpass | 2FA password, sent in Saved Messages. The message is deleted and the password is not stored |
| اسعار الهدايا | giftprices | List star gift prices and your balance |
| ارسل هدية | gift | Prepare a gift. Also `ارسل`. Nothing is sent yet |
| تأكيد الهدية | giftconfirm | Send the gift and spend Stars |
| الغاء الهدية | giftcancel | Discard the prepared gift |
| تفعيل اللعبة | gamewatch | Opt in: notify Saved Messages when a bot posts here |
| ايقاف اللعبة | gameoff | Stop watching this group |

Lock types: `الروابط` / `links`, `الوسائط` / `media`, `الصور` / `photos`, `الفيديو` / `videos`, `الملصقات` / `stickers`, `المتحركه` / `gifs`, `الصوت` / `voice`, `التوجيه` / `forwards`, `الدردشه` / `text`, `الكل` / `all`.

The account deletes a locked message only in groups where it can delete messages. It does not delete its own messages.

`تاك` and `اذاعة` go through the account limiter, stop when you send the cancel command, and cap how many chats or members they touch (settings `batch`, `max_members`, `max_targets`).

## Games

Automatic play in group-game chats (answering prompts to farm rewards) is not implemented. That uses the user account to compete in someone else's game and is the sort of unattended automation Telegram restricts. `.تفعيل اللعبة` only notifies Saved Messages, after a cooldown, so you can answer yourself. It never posts into the group.

## Not in this batch

Phase 4: downloads, sticker tools, converters (OCR, TTS, translate), and the remaining utility commands from the old userbot.
