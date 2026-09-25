"""Fernet encryption for managed-account session strings."""

from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken

from src.config import get_settings


class SessionDecryptError(Exception):
    """Raised when a stored session string cannot be decrypted with the current key."""


def _fernet() -> Fernet:
    return Fernet(get_settings().session_encryption_key.encode())


def is_encrypted_session(value: str | None) -> bool:
    """True when ``value`` is a Fernet token for the current key."""
    if not value or not value.startswith("gAAAA"):
        return False
    try:
        _fernet().decrypt(value.encode())
    except (InvalidToken, ValueError, TypeError):
        return False
    return True


def encrypt_session(value: str | None) -> str | None:
    """Encrypt a plaintext session. Empty values and existing tokens are unchanged."""
    if value is None or value == "":
        return value
    if is_encrypted_session(value):
        return value
    return _fernet().encrypt(value.encode()).decode()


def decrypt_session(value: str) -> str:
    """Decrypt a Fernet session token. Plaintext input is returned unchanged."""
    if value is None or value == "":
        return value
    if not value.startswith("gAAAA"):
        return value
    try:
        return _fernet().decrypt(value.encode()).decode()
    except (InvalidToken, ValueError, TypeError) as exc:
        if not is_encrypted_session(value):
            return value
        raise SessionDecryptError("Could not decrypt session string") from exc


def decrypt_session_value(value: str | None) -> str | None:
    if value is None:
        return None
    return decrypt_session(value)
