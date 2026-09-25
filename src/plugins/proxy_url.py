"""SOCKS URL for the proxy already assigned to a managed account."""

from __future__ import annotations

from urllib.parse import quote


def format_socks_url(parsed: dict | None) -> str | None:
    if not parsed:
        return None
    host = parsed.get("hostname")
    port = parsed.get("port")
    if not host or not port:
        return None
    user = parsed.get("username") or ""
    password = parsed.get("password") or ""
    if user:
        user_part = quote(str(user), safe="")
        password_part = quote(str(password), safe="")
        return f"socks5://{user_part}:{password_part}@{host}:{port}"
    return f"socks5://{host}:{port}"


def account_proxy_url(account_id: int) -> str | None:
    from src.code_monitor import build_proxy_dict
    from src.database import get_account_details

    details = get_account_details(account_id) or {}
    return format_socks_url(build_proxy_dict(details.get("proxy_string")))
