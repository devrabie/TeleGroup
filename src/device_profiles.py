from enum import Enum
from pyrogram import enums

try:
    ClientPlatform = enums.ClientPlatform
except AttributeError:
    class ClientPlatform(Enum):
        ANDROID = "android"
        IOS = "ios"
        DESKTOP = "desktop"

# A list of realistic device profiles to be used by the Pyrogram client.
# This helps in reducing the chance of an account being banned by Telegram.
# Based on: https://docs.pyrogram.org/faq/decreasing-chance-of-ban

# Specific API credentials for the official Android app
ANDROID_API_ID = 6
ANDROID_API_HASH = "eb06d4abfb49dc3eeb1aeb98ae0f581e"

DEVICES = [
    # --- Android Devices ---
    # Samsung
    {"api_id": ANDROID_API_ID, "api_hash": ANDROID_API_HASH, "device_model": "Samsung SM-S911B", "system_version": "14 (34)", "app_version": "11.13.2 (60601)", "lang_code": "en", "client_platform": ClientPlatform.ANDROID},
    {"api_id": ANDROID_API_ID, "api_hash": ANDROID_API_HASH, "device_model": "Samsung SM-S928B", "system_version": "15 (35)", "app_version": "11.13.2 (60601)", "lang_code": "en", "client_platform": ClientPlatform.ANDROID},
    {"api_id": ANDROID_API_ID, "api_hash": ANDROID_API_HASH, "device_model": "Samsung SM-A546B", "system_version": "13 (33)", "app_version": "11.13.2 (60601)", "lang_code": "en", "client_platform": ClientPlatform.ANDROID},
    {"api_id": ANDROID_API_ID, "api_hash": ANDROID_API_HASH, "device_model": "Samsung SM-M336B", "system_version": "13 (33)", "app_version": "11.13.2 (60601)", "lang_code": "en", "client_platform": ClientPlatform.ANDROID},
    {"api_id": ANDROID_API_ID, "api_hash": ANDROID_API_HASH, "device_model": "Samsung SM-G991B", "system_version": "12 (31)", "app_version": "11.13.2 (60601)", "lang_code": "en", "client_platform": ClientPlatform.ANDROID},

    # Xiaomi / Redmi
    {"api_id": ANDROID_API_ID, "api_hash": ANDROID_API_HASH, "device_model": "Xiaomi 2211133G", "system_version": "14 (34)", "app_version": "11.13.2 (60601)", "lang_code": "en", "client_platform": ClientPlatform.ANDROID},
    {"api_id": ANDROID_API_ID, "api_hash": ANDROID_API_HASH, "device_model": "Xiaomi 23049PCD8G", "system_version": "14 (34)", "app_version": "11.13.2 (60601)", "lang_code": "en", "client_platform": ClientPlatform.ANDROID},
    {"api_id": ANDROID_API_ID, "api_hash": ANDROID_API_HASH, "device_model": "Redmi Note 13 Pro 5G", "system_version": "13 (33)", "app_version": "11.13.2 (60601)", "lang_code": "en", "client_platform": ClientPlatform.ANDROID},
    {"api_id": ANDROID_API_ID, "api_hash": ANDROID_API_HASH, "device_model": "Redmi Note 12", "system_version": "12 (31)", "app_version": "11.13.2 (60601)", "lang_code": "en", "client_platform": ClientPlatform.ANDROID},
    {"api_id": ANDROID_API_ID, "api_hash": ANDROID_API_HASH, "device_model": "Redmi K60", "system_version": "14 (34)", "app_version": "11.13.2 (60601)", "lang_code": "en", "client_platform": ClientPlatform.ANDROID},

    # Huawei / Honor
    {"api_id": ANDROID_API_ID, "api_hash": ANDROID_API_HASH, "device_model": "HUAWEI ELE-L29", "system_version": "12 (31)", "app_version": "11.13.2 (60601)", "lang_code": "en", "client_platform": ClientPlatform.ANDROID},
    {"api_id": ANDROID_API_ID, "api_hash": ANDROID_API_HASH, "device_model": "HUAWEI ANA-NX9", "system_version": "13 (33)", "app_version": "11.13.2 (60601)", "lang_code": "en", "client_platform": ClientPlatform.ANDROID},
    {"api_id": ANDROID_API_ID, "api_hash": ANDROID_API_HASH, "device_model": "HUAWEI LYA-L29", "system_version": "11 (30)", "app_version": "11.13.2 (60601)", "lang_code": "en", "client_platform": ClientPlatform.ANDROID},
    {"api_id": ANDROID_API_ID, "api_hash": ANDROID_API_HASH, "device_model": "Honor VNE-N41", "system_version": "14 (34)", "app_version": "11.13.2 (60601)", "lang_code": "en", "client_platform": ClientPlatform.ANDROID},
    {"api_id": ANDROID_API_ID, "api_hash": ANDROID_API_HASH, "device_model": "Honor FNE-NX9", "system_version": "13 (33)", "app_version": "11.13.2 (60601)", "lang_code": "en", "client_platform": ClientPlatform.ANDROID},

    # OnePlus
    {"api_id": ANDROID_API_ID, "api_hash": ANDROID_API_HASH, "device_model": "OnePlus CPH2413", "system_version": "14 (34)", "app_version": "11.13.2 (60601)", "lang_code": "en", "client_platform": ClientPlatform.ANDROID},
    {"api_id": ANDROID_API_ID, "api_hash": ANDROID_API_HASH, "device_model": "OnePlus LE2113", "system_version": "12 (31)", "app_version": "11.13.2 (60601)", "lang_code": "en", "client_platform": ClientPlatform.ANDROID},
    {"api_id": ANDROID_API_ID, "api_hash": ANDROID_API_HASH, "device_model": "OnePlus IN2023", "system_version": "13 (33)", "app_version": "11.13.2 (60601)", "lang_code": "en", "client_platform": ClientPlatform.ANDROID},
    {"api_id": ANDROID_API_ID, "api_hash": ANDROID_API_HASH, "device_model": "OnePlus GM1913", "system_version": "11 (30)", "app_version": "11.13.2 (60601)", "lang_code": "en", "client_platform": ClientPlatform.ANDROID},

    # Oppo
    {"api_id": ANDROID_API_ID, "api_hash": ANDROID_API_HASH, "device_model": "OPPO CPH2573", "system_version": "13 (33)", "app_version": "11.13.2 (60601)", "lang_code": "en", "client_platform": ClientPlatform.ANDROID},
    {"api_id": ANDROID_API_ID, "api_hash": ANDROID_API_HASH, "device_model": "OPPO CPH2447", "system_version": "14 (34)", "app_version": "11.13.2 (60601)", "lang_code": "en", "client_platform": ClientPlatform.ANDROID},
    {"api_id": ANDROID_API_ID, "api_hash": ANDROID_API_HASH, "device_model": "OPPO CPH2305", "system_version": "12 (31)", "app_version": "11.13.2 (60601)", "lang_code": "en", "client_platform": ClientPlatform.ANDROID},
    {"api_id": ANDROID_API_ID, "api_hash": ANDROID_API_HASH, "device_model": "OPPO CPH2005", "system_version": "11 (30)", "app_version": "11.13.2 (60601)", "lang_code": "en", "client_platform": ClientPlatform.ANDROID},

    # Vivo
    {"api_id": ANDROID_API_ID, "api_hash": ANDROID_API_HASH, "device_model": "Vivo V2313A", "system_version": "14 (34)", "app_version": "11.13.2 (60601)", "lang_code": "en", "client_platform": ClientPlatform.ANDROID},
    {"api_id": ANDROID_API_ID, "api_hash": ANDROID_API_HASH, "device_model": "Vivo V2254A", "system_version": "13 (33)", "app_version": "11.13.2 (60601)", "lang_code": "en", "client_platform": ClientPlatform.ANDROID},
    {"api_id": ANDROID_API_ID, "api_hash": ANDROID_API_HASH, "device_model": "Vivo V2145", "system_version": "12 (31)", "app_version": "11.13.2 (60601)", "lang_code": "en", "client_platform": ClientPlatform.ANDROID},
    {"api_id": ANDROID_API_ID, "api_hash": ANDROID_API_HASH, "device_model": "Vivo V2061", "system_version": "11 (30)", "app_version": "11.13.2 (60601)", "lang_code": "en", "client_platform": ClientPlatform.ANDROID},

    # Realme
    {"api_id": ANDROID_API_ID, "api_hash": ANDROID_API_HASH, "device_model": "Realme RMX3771", "system_version": "14 (34)", "app_version": "11.13.2 (60601)", "lang_code": "en", "client_platform": ClientPlatform.ANDROID},
    {"api_id": ANDROID_API_ID, "api_hash": ANDROID_API_HASH, "device_model": "Realme RMX3563", "system_version": "13 (33)", "app_version": "11.13.2 (60601)", "lang_code": "en", "client_platform": ClientPlatform.ANDROID},
    {"api_id": ANDROID_API_ID, "api_hash": ANDROID_API_HASH, "device_model": "Realme RMX3461", "system_version": "12 (31)", "app_version": "11.13.2 (60601)", "lang_code": "en", "client_platform": ClientPlatform.ANDROID},
    {"api_id": ANDROID_API_ID, "api_hash": ANDROID_API_HASH, "device_model": "Realme RMX2170", "system_version": "11 (30)", "app_version": "11.13.2 (60601)", "lang_code": "en", "client_platform": ClientPlatform.ANDROID},

    # Motorola
    {"api_id": ANDROID_API_ID, "api_hash": ANDROID_API_HASH, "device_model": "moto g84 5G", "system_version": "14 (34)", "app_version": "11.13.2 (60601)", "lang_code": "en", "client_platform": ClientPlatform.ANDROID},
    {"api_id": ANDROID_API_ID, "api_hash": ANDROID_API_HASH, "device_model": "moto g73 5G", "system_version": "13 (33)", "app_version": "11.13.2 (60601)", "lang_code": "en", "client_platform": ClientPlatform.ANDROID},
    {"api_id": ANDROID_API_ID, "api_hash": ANDROID_API_HASH, "device_model": "moto g60", "system_version": "12 (31)", "app_version": "11.13.2 (60601)", "lang_code": "en", "client_platform": ClientPlatform.ANDROID},
    {"api_id": ANDROID_API_ID, "api_hash": ANDROID_API_HASH, "device_model": "moto g50", "system_version": "11 (30)", "app_version": "11.13.2 (60601)", "lang_code": "en", "client_platform": ClientPlatform.ANDROID},

    # Google Pixel
    {"api_id": ANDROID_API_ID, "api_hash": ANDROID_API_HASH, "device_model": "Pixel 8 Pro", "system_version": "15 (35)", "app_version": "11.13.2 (60601)", "lang_code": "en", "client_platform": ClientPlatform.ANDROID},
    {"api_id": ANDROID_API_ID, "api_hash": ANDROID_API_HASH, "device_model": "Pixel 7a", "system_version": "14 (34)", "app_version": "11.13.2 (60601)", "lang_code": "en", "client_platform": ClientPlatform.ANDROID},
    {"api_id": ANDROID_API_ID, "api_hash": ANDROID_API_HASH, "device_model": "Pixel 6", "system_version": "13 (33)", "app_version": "11.13.2 (60601)", "lang_code": "en", "client_platform": ClientPlatform.ANDROID},
    {"api_id": ANDROID_API_ID, "api_hash": ANDROID_API_HASH, "device_model": "Pixel 5", "system_version": "12 (31)", "app_version": "11.13.2 (60601)", "lang_code": "en", "client_platform": ClientPlatform.ANDROID},
]
