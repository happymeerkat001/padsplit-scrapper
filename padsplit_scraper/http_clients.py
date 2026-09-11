"""Shared HTTP session helpers. Importing this module does not open sockets."""

from __future__ import annotations

from typing import Optional

import requests

DEFAULT_TIMEOUT = 20


class TimeoutSession(requests.Session):
    """requests.Session that always applies a default timeout."""

    def __init__(self, timeout: float = DEFAULT_TIMEOUT) -> None:
        super().__init__()
        self.default_timeout = timeout

    def request(self, method, url, **kwargs):  # type: ignore[override]
        kwargs.setdefault("timeout", self.default_timeout)
        return super().request(method, url, **kwargs)


def build_session(*, timeout: float = DEFAULT_TIMEOUT, user_agent: Optional[str] = None) -> TimeoutSession:
    session = TimeoutSession(timeout=timeout)
    session.headers.setdefault("User-Agent", user_agent or "padsplit-scrapper/stage-ab")
    return session
