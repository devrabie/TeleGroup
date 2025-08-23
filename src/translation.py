import gettext
import os
from database import get_user_language

# Define the location of the locale files.
# os.path.dirname(__file__) -> /app/src
# os.path.join(..., '..') -> /app
# os.path.join(..., 'locales') -> /app/locales
LOCALE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'locales')

# A cache to store translation objects to avoid reading files on every request.
# This is a simple in-memory cache.
_translation_cache = {}

def get_translator(lang_code: str):
    """
    Returns a gettext translation object for a given language code.
    Caches the result to improve performance.
    """
    if lang_code in _translation_cache:
        return _translation_cache[lang_code]

    try:
        translation = gettext.translation(
            domain='base',  # This is the domain, matching our .mo file name
            localedir=LOCALE_DIR,
            languages=[lang_code],
            fallback=True # Fallback to default language if a string is not found
        )
    except FileNotFoundError:
        # Fallback to a null translator if the entire language file doesn't exist
        translation = gettext.NullTranslations()

    _translation_cache[lang_code] = translation
    return translation

def get_translation_func_for_user(user_id: int):
    """
    A simple helper to get the translation function for a specific user,
    often aliased as _.

    Usage:
    _ = get_translation_func_for_user(user_id)
    print(_("Hello, World!"))
    """
    lang_code = get_user_language(user_id)
    translator = get_translator(lang_code)
    return translator.gettext
