# Userbot commands

Outgoing messages from the managed account start with `USERBOT_PREFIX` (default `.`). Arabic is the primary name. English is an alias. Replies use one frame: a short header, the separator `⋆┄─┄─┄─┄┄─┄─┄─┄─┄┄⋆`, then the sentence. Arabic is used when the owner's control-bot language is Arabic. Any other language gets the English text.

## Sections and help

Commands are grouped into eight sections: الادارة، الحماية، التحميل، الوسائط، الأدوات، الحساب، الترفيه، النظام.

| Command | What it shows |
| --- | --- |
| `.الاوامر` / `.help` | The sections, then each enabled plugin's commands |
| `.الاوامر الادارة` | One section. English: `.help management` |
| `.الاوامر admin` | One plugin, with usage for each command |
| `.الاوامر حظر` | One command: description, usage, and an example |
| `.اللوحة` / `.panel` | The inline button panel in the current chat |
| `.الاوامر لوحة` | Same panel |

`.help panel` opens the panel too. The Arabic guide for the buttons is [panel-ar.md](panel-ar.md).

## Inline panel

`.اللوحة` asks the control bot, through inline mode, to post a message with buttons. The owner can browse sections, read a command, turn a plugin on or off for that account, and change simple settings (on/off, or a number). Plugins missing from the plan are shown with a lock and cannot be enabled. Only the managed account itself and the registered owner may press the buttons. Other people get an alert.

The toggle is written to `account_plugins` (or the existing group-creation and code-monitor columns). The worker reads enablement on every command, and background listeners re-check it about every two seconds. A `runtime_signals` row and Postgres `NOTIFY telegroup_runtime` wake that wait immediately, so a toggle applies without restarting the worker. Setting changes from the panel use the same wake-up.

### Enable inline mode once

The control bot must have inline mode turned on, or Telegram refuses the panel:

1. Open [@BotFather](https://t.me/BotFather).
2. Send `/setinline`.
3. Choose the control bot.
4. Send the placeholder shown while someone searches, for example `لوحة`.

Until that is done, `.اللوحة` replies with these same steps instead of a button panel.

Phase 4 commands are in the first table. Phase 3 commands follow. `.الاوامر download` (or `.help download`) prints one plugin's descriptions.

Alembic `0004_phase4` grants the phase 4 plugins to every existing plan and does not remove anything. New plans include them. Empty allowlists filled by the SQLite importer include them too.

## Phase 4

Downloads and conversions run in a separate process, at most `MEDIA_WORKERS` at a time (default 1, maximum 2). Each account defaults to one download at a time. Files larger than `DOWNLOAD_MAX_MB` (default 50) or longer than `DOWNLOAD_MAX_SECONDS` (default 600) are refused. Temporary files are deleted after the upload. The account's proxy is used when `use_proxy` is on.

`ffmpeg` is required for gif, voice, and mp3 conversion, and for YouTube audio extraction. OCR needs the optional `tesseract` package. Missing tools produce a message instead of a crash.

| Arabic | English | Description |
| --- | --- | --- |
| يوتيوب | yt | Download a YouTube video from a link or a search |
| اغنية | ytaudio | Download YouTube audio from a link or a search |
| بحث | ytsearch | Search YouTube and list results. Does not download |
| تيك | tiktok | Download a TikTok link |
| انستا | ig | Download an Instagram link |
| تحميل | dl | Download any public http(s) link with yt-dlp |
| ايقاف التحميل | dlstop | Stop this account's download |
| ملصق | kang | Add a replied sticker or image to this account's pack |
| معلومات الملصق | packinfo | Show the pack of a replied sticker |
| لملصق | tosticker | Convert a replied image to a sticker |
| لصورة | toimage | Convert a replied static sticker to an image |
| ترجمة | tr | Translate text or a reply. Also `translate`. Language code first |
| نطق | tts | Speak text or a reply. Language code first |
| استخراج | ocr | Read text from a replied image when tesseract is installed |
| لمتحرك | gif | Turn a replied video into a short gif (15 seconds) |
| بصمة | voice | Turn replied media into a voice note |
| لمقطع | tomp3 | Extract mp3 audio from a reply |
| تليجراف | telegraph | Upload text or a replied image to telegra.ph |
| معلومات | info | Show this chat, or a user from a reply, @username, or id |
| مغادرة | leave | Leave this group or channel |
| تكرار | repeat | Repeat short text. Count first. Hard cap 8 |
| ايقاف التكرار | repeatstop | Stop a repeat that is still sending |
| وضع الاسم | setname | Set the profile name. First word is the first name |
| وضع البايو | setbio | Set the bio from text or a reply (70 characters) |
| وضع الصورة | setphoto | Set the profile photo from a replied image |
| الوقت | time | Show the time in `DISPLAY_TIMEZONE` |
| التاريخ | date | Show the date in `DISPLAY_TIMEZONE` |
| احسب | calc | Arithmetic only: numbers, `+ - * / %`, and parentheses |

`.معلومات الملصق` is the pack command. `.معلومات` alone is the chat command. `.بحث` does not download; `.يوتيوب` and `.اغنية` do.

Animated and video stickers are not added to packs. Static images are resized to a 512-pixel webp sticker. Packs rotate after 120 stickers.

`.تكرار` waits between messages, refuses command text, and will not send more than 8 copies even if the setting is higher.

## Phase 3 commands

Outgoing messages from the managed account, starting with `USERBOT_PREFIX` (default `.`). Arabic is the primary name. English is an alias. `.الاوامر admin` (or `.help admin`) prints one plugin's descriptions.

Plugins are idle until you use them. PM protection, logging, and game notices stay off until you turn them on. Locks and replies do nothing until you add one. Broadcast and gifts ask for a second command before they spend messages or Stars.

Phase 3 plugins are not granted by revision 0003. Allow them in the plan editor if a plan was created before they existed. New plans include them. The SQLite importer grants them only when a plan's allowlist is still empty.

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

## Still not implemented

Automatic play in other people's group games, YouTube or Instagram cookies, age-restricted or private posts, animated sticker packs (TGS), and playlist downloads. See the phase 4 notes in the README.
