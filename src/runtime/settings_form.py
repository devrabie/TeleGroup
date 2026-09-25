"""Validate values for the generic plugin settings form."""

from __future__ import annotations

from typing import Any

from src.runtime.plugins import SettingField


def coerce_setting(field: SettingField, raw: str) -> tuple[bool, Any]:
    """Return ``(True, value)`` or ``(False, error code)``.

    A single hyphen resets the field to its default.
    """
    text = raw.strip()
    if text == "-":
        return True, field.default
    if field.kind == "bool":
        lowered = text.casefold()
        if lowered in {"1", "true", "on", "yes", "تشغيل"}:
            return True, True
        if lowered in {"0", "false", "off", "no", "ايقاف"}:
            return True, False
        return False, "invalid"
    if field.kind == "int":
        try:
            value = int(text)
        except ValueError:
            return False, "invalid"
        if field.minimum is not None and value < field.minimum:
            return False, "range"
        if field.maximum is not None and value > field.maximum:
            return False, "range"
        return True, value
    if field.kind == "str":
        if len(text) > 500:
            return False, "invalid"
        return True, text
    return False, "invalid"


def current_setting(account_id: int, plugin_name: str, field: SettingField) -> Any:
    from src.runtime.store import get_plugin_setting

    value = get_plugin_setting(account_id, plugin_name, field.key, field.default)
    if value is None:
        return field.default
    return value


def save_setting(account_id: int, plugin_name: str, field: SettingField, value: Any) -> None:
    from src.runtime.store import set_plugin_setting

    set_plugin_setting(account_id, plugin_name, field.key, value)
