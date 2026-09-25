"""Command sections, plugin labels, and per-command usage.

``{p}`` is the account command prefix. Descriptions stay on each plugin.
Usage and examples are edited here so help text and the panel stay in one place.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Category:
    id: str
    title_ar: str
    title_en: str
    emoji: str
    aliases: tuple[str, ...]

    def title(self, language: str) -> str:
        if language == "ar":
            return self.title_ar
        return self.title_en

    def label(self, language: str) -> str:
        return f"{self.title(language)} {self.emoji}"


CATEGORIES: tuple[Category, ...] = (
    Category("admin", "الادارة", "Management", "🛡", ("الادارة", "الإدارة", "management")),
    Category("guard", "الحماية", "Protection", "🔒", ("الحماية", "protection", "guard")),
    Category("download", "التحميل", "Downloads", "📥", ("التحميل", "downloads", "download")),
    Category("media", "الوسائط", "Media", "🎨", ("الوسائط", "media")),
    Category("tools", "الأدوات", "Tools", "🛠", ("الأدوات", "الادوات", "tools")),
    Category("account", "الحساب", "Account", "👤", ("الحساب", "account")),
    Category("fun", "الترفيه", "Fun", "🎮", ("الترفيه", "fun")),
    Category("system", "النظام", "System", "⚙️", ("النظام", "system")),
)

CATEGORY_BY_ID = {item.id: item for item in CATEGORIES}

PLUGIN_CATEGORY: dict[str, str] = {
    "admin": "admin",
    "tagall": "admin",
    "broadcast": "admin",
    "create": "admin",
    "groups": "admin",
    "leave": "admin",
    "locks": "guard",
    "pmpermit": "guard",
    "afk": "guard",
    "codemon": "guard",
    "download": "download",
    "stickers": "media",
    "convert": "media",
    "tts": "media",
    "ocr": "media",
    "telegraph": "media",
    "calc": "tools",
    "clock": "tools",
    "info": "tools",
    "id": "tools",
    "ping": "tools",
    "translate": "tools",
    "profile": "account",
    "storage": "account",
    "autoreply": "account",
    "repeat": "fun",
    "games": "fun",
    "gifts": "fun",
    "help": "system",
    "delegates": "system",
}

PLUGIN_LABEL: dict[str, tuple[str, str]] = {
    "admin": ("Moderation", "الإشراف"),
    "tagall": ("Mentions", "الإشارة"),
    "broadcast": ("Broadcast", "الإذاعة"),
    "create": ("Create chat", "إنشاء محادثة"),
    "groups": ("Group schedule", "جدول المجموعات"),
    "leave": ("Leave", "المغادرة"),
    "locks": ("Locks", "الأقفال"),
    "pmpermit": ("PM guard", "حماية الخاص"),
    "afk": ("Away", "الغياب"),
    "codemon": ("Login codes", "أكواد الدخول"),
    "download": ("Downloads", "التحميل"),
    "stickers": ("Stickers", "الملصقات"),
    "convert": ("Convert", "التحويل"),
    "tts": ("Speech", "النطق"),
    "ocr": ("OCR", "استخراج النص"),
    "telegraph": ("Telegraph", "تيليغراف"),
    "calc": ("Calculator", "الحاسبة"),
    "clock": ("Clock", "الوقت"),
    "info": ("Chat info", "المعلومات"),
    "id": ("Ids", "المعرّف"),
    "ping": ("Ping", "الفحص"),
    "translate": ("Translate", "الترجمة"),
    "profile": ("Profile", "الملف"),
    "storage": ("Logging", "التخزين"),
    "autoreply": ("Replies", "الردود"),
    "repeat": ("Repeat", "التكرار"),
    "games": ("Games", "الألعاب"),
    "gifts": ("Gifts", "الهدايا"),
    "help": ("Help", "الأوامر"),
    "delegates": ("Command admins", "مسؤولو الأوامر"),
}

# name -> (usage_en, usage_ar, example_en, example_ar)
COMMAND_HELP: dict[str, tuple[str, str, str, str]] = {}


def _row(usage_en: str, usage_ar: str, example_en: str, example_ar: str, *names: str) -> None:
    packed = (usage_en, usage_ar, example_en, example_ar)
    for name in names:
        if name in COMMAND_HELP:
            raise RuntimeError(f"Duplicate command help for {name}")
        COMMAND_HELP[name] = packed


_row("{p}ping", "{p}فحص", "{p}ping", "{p}فحص", "ping", "فحص")
_row("{p}id", "{p}ايدي", "{p}id", "{p}ايدي", "id", "ايدي")
_row(
    "{p}help [section|plugin|command]",
    "{p}الاوامر [قسم|اضافة|امر]",
    "{p}help ban",
    "{p}الاوامر حظر",
    "help",
    "الاوامر",
    "الأوامر",
)
_row("{p}panel", "{p}اللوحة", "{p}panel", "{p}اللوحة", "panel", "اللوحة")
_row(
    "{p}addadmin by reply, id, or @username",
    "{p}رفع ادمن بالرد أو المعرّف أو @username",
    "{p}addadmin 123456",
    "{p}رفع ادمن 123456",
    "رفع ادمن",
    "addadmin",
)
_row(
    "{p}deladmin by reply, id, or @username",
    "{p}تنزيل ادمن بالرد أو المعرّف أو @username",
    "{p}deladmin 123456",
    "{p}تنزيل ادمن 123456",
    "تنزيل ادمن",
    "deladmin",
)
_row("{p}admins", "{p}الادمنية", "{p}admins", "{p}الادمنية", "الادمنية", "admins")

_row(
    "{p}ban by reply, or {p}ban @user",
    "{p}حظر بالرد، أو {p}حظر @user",
    "{p}ban",
    "{p}حظر",
    "حظر",
    "ban",
)
_row(
    "{p}unban by reply, or {p}unban @user",
    "{p}الغاء الحظر بالرد، أو {p}الغاء الحظر @user",
    "{p}unban",
    "{p}الغاء الحظر",
    "الغاء الحظر",
    "unban",
)
_row(
    "{p}kick by reply, or {p}kick @user",
    "{p}طرد بالرد، أو {p}طرد @user",
    "{p}kick",
    "{p}طرد",
    "طرد",
    "kick",
)
_row(
    "{p}mute by reply, or {p}mute @user",
    "{p}كتم بالرد، أو {p}كتم @user",
    "{p}mute",
    "{p}كتم",
    "كتم",
    "mute",
)
_row(
    "{p}unmute by reply, or {p}unmute @user",
    "{p}الغاء الكتم بالرد، أو {p}الغاء الكتم @user",
    "{p}unmute",
    "{p}الغاء الكتم",
    "الغاء الكتم",
    "unmute",
)
_row(
    "{p}promote by reply, or {p}promote @user",
    "{p}رفع مشرف بالرد، أو {p}رفع مشرف @user",
    "{p}promote",
    "{p}رفع مشرف",
    "رفع مشرف",
    "promote",
)
_row(
    "{p}demote by reply, or {p}demote @user",
    "{p}تنزيل مشرف بالرد، أو {p}تنزيل مشرف @user",
    "{p}demote",
    "{p}تنزيل مشرف",
    "تنزيل مشرف",
    "demote",
)
_row("{p}pin by reply", "{p}تثبيت بالرد", "{p}pin", "{p}تثبيت", "تثبيت", "pin")
_row(
    "{p}unpin by reply",
    "{p}الغاء التثبيت بالرد",
    "{p}unpin",
    "{p}الغاء التثبيت",
    "الغاء التثبيت",
    "unpin",
)
_row("{p}del by reply", "{p}مسح بالرد", "{p}del", "{p}مسح", "مسح", "del")
_row(
    "{p}purge by reply to the first message",
    "{p}تنظيف بالرد على أول رسالة",
    "{p}purge",
    "{p}تنظيف",
    "تنظيف",
    "purge",
)

_row(
    "{p}storage [on|off]",
    "{p}تخزين [تشغيل|ايقاف]",
    "{p}storage on",
    "{p}تخزين تشغيل",
    "تخزين",
    "storage",
)
_row(
    "{p}setlog here|me|chat id",
    "{p}وضع التخزين هنا|محفوظات|المعرّف",
    "{p}setlog me",
    "{p}وضع التخزين محفوظات",
    "وضع التخزين",
    "setlog",
)

_row(
    "{p}gfilter keyword | reply",
    "{p}رد عام الكلمة | الرد",
    "{p}gfilter hello | hi",
    "{p}رد عام مرحبا | أهلاً",
    "رد عام",
    "gfilter",
)
_row(
    "{p}ungfilter keyword",
    "{p}حذف رد عام الكلمة",
    "{p}ungfilter hello",
    "{p}حذف رد عام مرحبا",
    "حذف رد عام",
    "ungfilter",
)
_row(
    "{p}gfilters",
    "{p}الردود العامة",
    "{p}gfilters",
    "{p}الردود العامة",
    "الردود العامة",
    "gfilters",
)
_row(
    "{p}filter keyword | reply",
    "{p}رد الكلمة | الرد",
    "{p}filter hello | hi",
    "{p}رد مرحبا | أهلاً",
    "رد",
    "filter",
)
_row(
    "{p}unfilter keyword",
    "{p}حذف رد الكلمة",
    "{p}unfilter hello",
    "{p}حذف رد مرحبا",
    "حذف رد",
    "unfilter",
)
_row("{p}filters", "{p}الردود", "{p}filters", "{p}الردود", "الردود", "filters")

_row(
    "{p}afk optional message",
    "{p}غائب رسالة اختيارية",
    "{p}afk back soon",
    "{p}غائب أعود قريباً",
    "غائب",
    "afk",
)
_row("{p}unafk", "{p}الغاء الغياب", "{p}unafk", "{p}الغاء الغياب", "الغاء الغياب", "unafk")

_row(
    "{p}pmpermit [on|off]",
    "{p}الحماية [تشغيل|ايقاف]",
    "{p}pmpermit on",
    "{p}الحماية تشغيل",
    "الحماية",
    "pmpermit",
)
_row(
    "{p}approve by reply or id",
    "{p}سماح بالرد أو المعرّف",
    "{p}approve",
    "{p}سماح",
    "سماح",
    "approve",
)
_row(
    "{p}disapprove by reply or id",
    "{p}رفض بالرد أو المعرّف",
    "{p}disapprove",
    "{p}رفض",
    "رفض",
    "disapprove",
)
_row(
    "{p}pmwarn 1-10",
    "{p}عدد التحذير 1-10",
    "{p}pmwarn 3",
    "{p}عدد التحذير 3",
    "عدد التحذير",
    "pmwarn",
)

_row(
    "{p}lock links|media|photos|all",
    "{p}قفل الروابط|الوسائط|الصور|الكل",
    "{p}lock links",
    "{p}قفل الروابط",
    "قفل",
    "lock",
)
_row(
    "{p}unlock links",
    "{p}فتح الروابط",
    "{p}unlock links",
    "{p}فتح الروابط",
    "فتح",
    "unlock",
)
_row("{p}locks", "{p}الاقفال", "{p}locks", "{p}الاقفال", "الاقفال", "locks")

_row(
    "{p}tagall optional text",
    "{p}تاك نص اختياري",
    "{p}tagall meeting",
    "{p}تاك اجتماع",
    "تاك",
    "tagall",
)
_row("{p}tagstop", "{p}ايقاف التاك", "{p}tagstop", "{p}ايقاف التاك", "ايقاف التاك", "tagstop")

_row(
    "{p}broadcast message",
    "{p}اذاعة الرسالة",
    "{p}broadcast hello",
    "{p}اذاعة مرحبا",
    "اذاعة",
    "broadcast",
)
_row(
    "{p}pbroadcast message",
    "{p}اذاعة خاص الرسالة",
    "{p}pbroadcast hello",
    "{p}اذاعة خاص مرحبا",
    "اذاعة خاص",
    "pbroadcast",
)
_row(
    "{p}confirmbroadcast",
    "{p}تأكيد الاذاعة",
    "{p}confirmbroadcast",
    "{p}تأكيد الاذاعة",
    "تأكيد الاذاعة",
    "confirmbroadcast",
)
_row(
    "{p}broadcaststop",
    "{p}ايقاف الاذاعة",
    "{p}broadcaststop",
    "{p}ايقاف الاذاعة",
    "ايقاف الاذاعة",
    "broadcaststop",
)

_row(
    "{p}creategroup title",
    "{p}انشاء كروب الاسم",
    "{p}creategroup Weekend",
    "{p}انشاء كروب الجمعة",
    "انشاء كروب",
    "انشاء مجموعة",
    "create group",
    "creategroup",
)
_row(
    "{p}createchannel title",
    "{p}انشاء قناة الاسم",
    "{p}createchannel News",
    "{p}انشاء قناة الأخبار",
    "انشاء قناة",
    "create channel",
    "createchannel",
)
_row(
    "{p}transfer by reply or @user",
    "{p}نقل ملكية بالرد أو @user",
    "{p}transfer",
    "{p}نقل ملكية",
    "نقل ملكية",
    "نقل",
    "transfer",
)
_row(
    "{p}cloudpass your 2FA password (Saved Messages)",
    "{p}كلمة السر كلمة التحقق (في المحفوظات)",
    "{p}cloudpass",
    "{p}كلمة السر",
    "كلمة السر",
    "cloudpass",
)

_row(
    "{p}giftprices",
    "{p}اسعار الهدايا",
    "{p}giftprices",
    "{p}اسعار الهدايا",
    "اسعار الهدايا",
    "giftprices",
)
_row(
    "{p}gift id by reply",
    "{p}ارسل هدية المعرّف بالرد",
    "{p}gift 15",
    "{p}ارسل هدية 15",
    "ارسل هدية",
    "ارسل",
    "gift",
)
_row(
    "{p}giftconfirm",
    "{p}تأكيد الهدية",
    "{p}giftconfirm",
    "{p}تأكيد الهدية",
    "تأكيد الهدية",
    "giftconfirm",
)
_row(
    "{p}giftcancel",
    "{p}الغاء الهدية",
    "{p}giftcancel",
    "{p}الغاء الهدية",
    "الغاء الهدية",
    "giftcancel",
)

_row(
    "{p}gamewatch inside the group",
    "{p}تفعيل اللعبة داخل المجموعة",
    "{p}gamewatch",
    "{p}تفعيل اللعبة",
    "تفعيل اللعبة",
    "gamewatch",
)
_row("{p}gameoff", "{p}ايقاف اللعبة", "{p}gameoff", "{p}ايقاف اللعبة", "ايقاف اللعبة", "gameoff")

_row(
    "{p}yt link or search",
    "{p}يوتيوب رابط أو بحث",
    "{p}yt lofi",
    "{p}يوتيوب lofi",
    "يوتيوب",
    "yt",
)
_row(
    "{p}ytaudio link or search",
    "{p}اغنية رابط أو بحث",
    "{p}ytaudio lofi",
    "{p}اغنية lofi",
    "اغنية",
    "ytaudio",
)
_row("{p}ytsearch text", "{p}بحث النص", "{p}ytsearch lofi", "{p}بحث lofi", "بحث", "ytsearch")
_row(
    "{p}tiktok url",
    "{p}تيك الرابط",
    "{p}tiktok https://www.tiktok.com/t/1",
    "{p}تيك https://www.tiktok.com/t/1",
    "تيك",
    "tiktok",
)
_row(
    "{p}ig url",
    "{p}انستا الرابط",
    "{p}ig https://www.instagram.com/reel/1",
    "{p}انستا الرابط",
    "انستا",
    "ig",
)
_row(
    "{p}dl https://...",
    "{p}تحميل https://...",
    "{p}dl https://example.com/a",
    "{p}تحميل https://example.com/a",
    "تحميل",
    "dl",
)
_row("{p}dlstop", "{p}ايقاف التحميل", "{p}dlstop", "{p}ايقاف التحميل", "ايقاف التحميل", "dlstop")

_row("{p}kang by reply", "{p}ملصق بالرد", "{p}kang", "{p}ملصق", "ملصق", "kang")
_row(
    "{p}packinfo by reply",
    "{p}معلومات الملصق بالرد",
    "{p}packinfo",
    "{p}معلومات الملصق",
    "معلومات الملصق",
    "packinfo",
)
_row(
    "{p}tosticker by reply",
    "{p}لملصق بالرد",
    "{p}tosticker",
    "{p}لملصق",
    "لملصق",
    "tosticker",
)
_row("{p}toimage by reply", "{p}لصورة بالرد", "{p}toimage", "{p}لصورة", "لصورة", "toimage")

_row(
    "{p}tr en text, or reply",
    "{p}ترجمة en النص، أو بالرد",
    "{p}tr en مرحبا",
    "{p}ترجمة en مرحبا",
    "ترجمة",
    "tr",
    "translate",
)
_row(
    "{p}tts text, or reply",
    "{p}نطق النص، أو بالرد",
    "{p}tts مرحبا",
    "{p}نطق مرحبا",
    "نطق",
    "tts",
)
_row(
    "{p}ocr by reply to an image",
    "{p}استخراج بالرد على صورة",
    "{p}ocr",
    "{p}استخراج",
    "استخراج",
    "ocr",
)
_row("{p}gif by reply", "{p}لمتحرك بالرد", "{p}gif", "{p}لمتحرك", "لمتحرك", "gif")
_row("{p}voice by reply", "{p}بصمة بالرد", "{p}voice", "{p}بصمة", "بصمة", "voice")
_row("{p}tomp3 by reply", "{p}لمقطع بالرد", "{p}tomp3", "{p}لمقطع", "لمقطع", "tomp3")
_row(
    "{p}telegraph text, or reply to an image",
    "{p}تليجراف النص، أو بالرد على صورة",
    "{p}telegraph hello",
    "{p}تليجراف مرحبا",
    "تليجراف",
    "telegraph",
)
_row(
    "{p}info, or {p}info @user",
    "{p}معلومات، أو {p}معلومات @user",
    "{p}info",
    "{p}معلومات",
    "معلومات",
    "info",
)
_row("{p}leave", "{p}مغادرة", "{p}leave", "{p}مغادرة", "مغادرة", "leave")
_row(
    "{p}repeat count text",
    "{p}تكرار العدد النص",
    "{p}repeat 2 hello",
    "{p}تكرار 2 مرحبا",
    "تكرار",
    "repeat",
)
_row(
    "{p}repeatstop",
    "{p}ايقاف التكرار",
    "{p}repeatstop",
    "{p}ايقاف التكرار",
    "ايقاف التكرار",
    "repeatstop",
)
_row(
    "{p}setname first last",
    "{p}وضع الاسم الأول الأخير",
    "{p}setname Ada Lovelace",
    "{p}وضع الاسم آدم",
    "وضع الاسم",
    "setname",
)
_row(
    "{p}setbio text, or reply",
    "{p}وضع البايو النص، أو بالرد",
    "{p}setbio hello",
    "{p}وضع البايو مرحبا",
    "وضع البايو",
    "setbio",
)
_row(
    "{p}setphoto by reply",
    "{p}وضع الصورة بالرد",
    "{p}setphoto",
    "{p}وضع الصورة",
    "وضع الصورة",
    "setphoto",
)
_row("{p}time", "{p}الوقت", "{p}time", "{p}الوقت", "الوقت", "time")
_row("{p}date", "{p}التاريخ", "{p}date", "{p}التاريخ", "التاريخ", "date")
_row("{p}calc 2*(3+4)", "{p}احسب 2*(3+4)", "{p}calc (2+3)*4", "{p}احسب (2+3)*4", "احسب", "calc")


def help_for(name: str) -> tuple[str, str, str, str]:
    return COMMAND_HELP.get(name, ("", "", "", ""))


def category_of(plugin_name: str) -> Category:
    found = CATEGORY_BY_ID.get(PLUGIN_CATEGORY.get(plugin_name, "tools"))
    if found is None:
        return CATEGORY_BY_ID["tools"]
    return found


def plugin_label(plugin_name: str, language: str) -> str:
    pair = PLUGIN_LABEL.get(plugin_name)
    if pair is None:
        return plugin_name
    if language == "ar":
        return pair[1]
    return pair[0]


def category_from_query(text: str) -> Category | None:
    folded = text.casefold().strip()
    for item in CATEGORIES:
        names = {item.id, item.title_ar, item.title_en.casefold(), *item.aliases}
        if folded in names or text.strip() in names:
            return item
    return None
