import gettext
import logging
import os
from pathlib import Path

from babel.messages.mofile import write_mo
from babel.messages.pofile import read_po

from src.database import get_user_language

log = logging.getLogger(__name__)

# Define the location of the locale files.
# os.path.dirname(__file__) -> /app/src
# os.path.join(..., '..') -> /app
# os.path.join(..., 'locales') -> /app/locales
LOCALE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'locales')

# A cache to store translation objects to avoid reading files on every request.
# This is a simple in-memory cache.
_translation_cache = {}
_compiled = False


def compile_translations(force: bool = False) -> int:
    """
    Compile ``*.po`` catalogs to ``*.mo`` files that gettext can load.

    ``.mo`` files are gitignored, so without this step the UI stays in English
    even when Arabic translations exist in the ``.po`` files.
    """
    global _compiled
    locales_root = Path(LOCALE_DIR)
    compiled = 0

    if not locales_root.is_dir():
        log.warning(f"Locale directory does not exist: {locales_root}")
        return 0

    for po_file in locales_root.glob("*/LC_MESSAGES/*.po"):
        mo_file = po_file.with_suffix(".mo")
        if (
            not force
            and mo_file.exists()
            and mo_file.stat().st_mtime >= po_file.stat().st_mtime
        ):
            continue
        try:
            with po_file.open("rb") as fh:
                catalog = read_po(fh)
            mo_file.parent.mkdir(parents=True, exist_ok=True)
            with mo_file.open("wb") as fh:
                write_mo(fh, catalog)
            compiled += 1
            log.info(f"Compiled translation catalog: {po_file} -> {mo_file}")
        except Exception as e:
            log.error(f"Failed to compile translation file {po_file}: {e}", exc_info=True)

    if compiled:
        _translation_cache.clear()
    _compiled = True
    return compiled


def get_translator(lang_code: str):
    """
    Returns a gettext translation object for a given language code.
    Caches the result to improve performance.
    """
    if not _compiled:
        compile_translations()

    if lang_code in _translation_cache:
        return _translation_cache[lang_code]

    try:
        translation = gettext.translation(
            domain='base',  # This is the domain, matching our .mo file name
            localedir=LOCALE_DIR,
            languages=[lang_code],
            fallback=True  # Fallback to msgid if a string is not found
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
