"""Account runtime seam. Phase 2 plugins should build Kurigram clients from here."""

from src.runtime.client_factory import build_user_client

__all__ = ["build_user_client"]
