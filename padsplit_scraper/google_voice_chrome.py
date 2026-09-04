#!/usr/bin/env python3
"""Google Voice group SMS via Ang's already-signed-in Mac Chrome profile.

Primary field-MMS transport. Uses Playwright against a persistent Chrome
user-data-dir (no Google Voice API key; never prompt for a password).
Mac-only. CI / box / VPS must not send — Google may challenge unfamiliar IPs.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Optional, Protocol, Sequence

VOICE_MESSAGES_URL = "https://voice.google.com/u/0/messages"

# Chrome root that contains Default / Profile N. Not the profile folder itself.
DEFAULT_CHROME_USER_DATA_DIR = (
    Path.home() / "Library" / "Application Support" / "Google" / "Chrome"
)
# Chrome profile directory name for the mr.angli session already signed into Voice.
DEFAULT_CHROME_PROFILE_DIRECTORY = "Default"

COMPOSE_SELECTORS = (
    'button:has-text("Send a message")',
    '[aria-label="Send a message"]',
    '[aria-label="New message"]',
    'button[aria-label*="Send a message" i]',
    'button[aria-label*="New message" i]',
    '[data-e2e-action="compose"]',
    '[gv-test-id="send-new-message"]',
)
RECIPIENT_SELECTORS = (
    'input[placeholder*="name or phone" i]',
    'input[aria-label*="name or phone" i]',
    'input[aria-label*="recipient" i]',
    'input[placeholder*="recipient" i]',
    'input[gv-test-id="recipient-input"]',
    'input[aria-label="To"]',
)
BODY_SELECTORS = (
    'textarea[aria-label*="Type a message" i]',
    'textarea[placeholder*="Type a message" i]',
    'div[aria-label*="Type a message" i][contenteditable="true"]',
    'textarea[aria-label*="message" i]',
    '[gv-test-id="message-input"]',
    'div[contenteditable="true"][aria-label*="message" i]',
)
SEND_SELECTORS = (
    'button[aria-label="Send message"]',
    'button[aria-label="Send"]',
    'button[aria-label*="Send message" i]',
    'button[gv-test-id="send-message"]',
    'button:has-text("Send")',
)
CHIP_SELECTORS = (
    "[gv-recipient-chip]",
    "gv-recipient-chip",
    '[data-recipient]',
    '[role="listitem"] [aria-label*="Remove"]',
    'button[aria-label*="Remove" i]',
)

_CHALLENGE_URL_HINTS = (
    "accounts.google.com",
    "signin/challenge",
    "deniedsigninrejected",
    "/recaptcha/",
    "sorry/index",
    "https://www.google.com/sorry",
)
_CHALLENGE_TEXT_STRONG = (
    "verify it's you",
    "verify it’s you",
    "verify that it's you",
    "verify that it’s you",
    "unusual activity",
    "couldn't sign you in",
    "couldn’t sign you in",
    "complete a security check",
    "confirm you're not a robot",
    "confirm you’re not a robot",
    "i'm not a robot",
    "i’m not a robot",
    "enter the letters you see",
    "recaptcha",
    "captcha",
)


class GoogleVoiceChallenge(Exception):
    """Google Voice showed a login wall, captcha, or account challenge."""


class GoogleVoiceTransportError(RuntimeError):
    """Browser or UI failure talking to Google Voice (not a login challenge)."""


class GoogleVoicePage(Protocol):
    def current_url(self) -> str: ...
    def page_title(self) -> str: ...
    def page_text(self) -> str: ...
    def goto_messages(self) -> None: ...
    def open_compose(self) -> None: ...
    def add_recipient(self, phone: str) -> None: ...
    def recipient_chip_count(self) -> int: ...
    def set_body(self, body: str) -> None: ...
    def click_send(self) -> None: ...
    def close(self) -> None: ...


def resolve_chrome_user_data_dir() -> Path:
    raw = (os.getenv("FIELD_MMS_CHROME_USER_DATA_DIR") or "").strip()
    if raw:
        return Path(raw).expanduser()
    return DEFAULT_CHROME_USER_DATA_DIR


def resolve_chrome_profile_directory() -> str:
    raw = os.getenv("FIELD_MMS_CHROME_PROFILE_DIRECTORY")
    if raw is None:
        return DEFAULT_CHROME_PROFILE_DIRECTORY
    stripped = raw.strip()
    return stripped or DEFAULT_CHROME_PROFILE_DIRECTORY


def format_voice_recipient(value: str) -> str:
    digits = re.sub(r"\D", "", value or "")
    if digits.startswith("1") and len(digits) == 11:
        digits = digits[1:]
    if len(digits) == 10:
        return f"({digits[0:3]}) {digits[3:6]}-{digits[6:10]}"
    return value


def detect_google_voice_challenge(
    *,
    url: str = "",
    title: str = "",
    visible_text: str = "",
) -> bool:
    """True when Voice bounced to a login wall, captcha, or account challenge."""
    url_l = (url or "").lower()
    title_l = (title or "").lower()
    text_l = (visible_text or "").lower()
    if any(hint in url_l for hint in _CHALLENGE_URL_HINTS):
        return True
    blob = f"{title_l}\n{text_l}"
    if any(hint in blob for hint in _CHALLENGE_TEXT_STRONG):
        return True
    if "voice.google.com" in url_l:
        return False
    if "sign in" in title_l or "sign in to continue" in text_l:
        return True
    if "use your google account" in blob:
        return True
    return False


def raise_if_google_voice_challenge(page: GoogleVoicePage) -> None:
    if detect_google_voice_challenge(
        url=page.current_url(),
        title=page.page_title(),
        visible_text=page.page_text(),
    ):
        raise GoogleVoiceChallenge("Google Voice login or challenge wall")


def send_on_google_voice_page(
    page: GoogleVoicePage,
    body: str,
    recipients: Sequence[str],
) -> None:
    """Compose one group message (not three 1:1s) and send it."""
    page.goto_messages()
    raise_if_google_voice_challenge(page)
    page.open_compose()
    raise_if_google_voice_challenge(page)
    for phone in recipients:
        page.add_recipient(phone)
    chips = page.recipient_chip_count()
    if 0 < chips < 3:
        raise GoogleVoiceTransportError(
            "Google Voice compose did not keep all three recipients"
        )
    page.set_body(body)
    page.click_send()
    raise_if_google_voice_challenge(page)


def send_via_google_voice_chrome(
    body: str,
    recipients: Sequence[str],
    *,
    voice_page: Optional[GoogleVoicePage] = None,
) -> None:
    """Send one group SMS via voice.google.com in a persistent Chrome profile."""
    try:
        from padsplit_scraper.field_mms import assert_group_recipients, sending_allowed
    except ModuleNotFoundError:  # python3 padsplit_scraper/field_mms.py
        from field_mms import assert_group_recipients, sending_allowed  # type: ignore

    checked = assert_group_recipients(recipients)
    if not sending_allowed():
        raise RuntimeError("CI / non-Mac must not send MMS")
    page = voice_page or launch_playwright_voice_page()
    owned = voice_page is None
    try:
        send_on_google_voice_page(page, body, checked)
    finally:
        if owned:
            page.close()


def launch_playwright_voice_page() -> GoogleVoicePage:
    user_data_dir = resolve_chrome_user_data_dir()
    if not user_data_dir.exists():
        raise GoogleVoiceTransportError(
            "Chrome user-data-dir missing; set FIELD_MMS_CHROME_USER_DATA_DIR "
            "to a profile already signed into Google Voice"
        )
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise GoogleVoiceTransportError(
            "Playwright is not installed. On the Mac: pip install playwright"
        ) from exc

    profile = resolve_chrome_profile_directory()
    headless = (os.getenv("FIELD_MMS_CHROME_HEADLESS") or "").strip().lower() in {
        "1",
        "true",
        "yes",
    }
    try:
        playwright = sync_playwright().start()
        context = playwright.chromium.launch_persistent_context(
            user_data_dir=str(user_data_dir),
            channel="chrome",
            headless=headless,
            args=[
                f"--profile-directory={profile}",
                "--disable-session-crashed-bubble",
                "--hide-crash-restore-bubble",
            ],
            ignore_default_args=["--enable-automation"],
            timeout=30000,
        )
        page = context.pages[0] if context.pages else context.new_page()
        context.set_default_timeout(20000)
        return PlaywrightVoicePage(playwright, context, page)
    except GoogleVoiceChallenge:
        raise
    except GoogleVoiceTransportError:
        raise
    except Exception as exc:
        detail = str(exc).lower()
        if "user data" in detail and ("in use" in detail or "lock" in detail or "profile" in detail):
            raise GoogleVoiceTransportError(
                "Chrome user-data-dir is locked; use a dedicated "
                "FIELD_MMS_CHROME_USER_DATA_DIR already signed into Voice"
            ) from exc
        raise GoogleVoiceTransportError("Google Voice Chrome failed to launch") from exc


class PlaywrightVoicePage:
    def __init__(self, playwright: Any, context: Any, page: Any) -> None:
        self._playwright = playwright
        self._context = context
        self._page = page

    def current_url(self) -> str:
        try:
            return str(self._page.url or "")
        except Exception:
            return ""

    def page_title(self) -> str:
        try:
            return str(self._page.title() or "")
        except Exception:
            return ""

    def page_text(self) -> str:
        try:
            return (self._page.inner_text("body") or "")[:8000]
        except Exception:
            return ""

    def goto_messages(self) -> None:
        self._page.goto(VOICE_MESSAGES_URL, wait_until="domcontentloaded", timeout=30000)
        try:
            self._page.wait_for_load_state("networkidle", timeout=8000)
        except Exception:
            pass

    def open_compose(self) -> None:
        if self._first_visible(RECIPIENT_SELECTORS, timeout_ms=1500) is not None:
            return
        if self._click_first(COMPOSE_SELECTORS, timeout_ms=8000):
            return
        if self._first_visible(RECIPIENT_SELECTORS, timeout_ms=2000) is not None:
            return
        raise_if_google_voice_challenge(self)
        raise GoogleVoiceTransportError("Google Voice compose control not found")

    def add_recipient(self, phone: str) -> None:
        locator = self._first_visible(RECIPIENT_SELECTORS, timeout_ms=8000)
        if locator is None:
            raise GoogleVoiceTransportError("Google Voice recipient field not found")
        locator.click()
        locator.fill("")
        locator.fill(format_voice_recipient(phone))
        locator.press("Enter")
        try:
            self._page.wait_for_timeout(400)
        except Exception:
            pass

    def recipient_chip_count(self) -> int:
        for selector in CHIP_SELECTORS:
            try:
                count = self._page.locator(selector).count()
            except Exception:
                continue
            if count:
                return int(count)
        return 0

    def set_body(self, body: str) -> None:
        locator = self._first_visible(BODY_SELECTORS, timeout_ms=8000)
        if locator is None:
            raise GoogleVoiceTransportError("Google Voice message box not found")
        locator.click()
        try:
            locator.fill(body)
        except Exception:
            locator.press_sequentially(body, delay=5)

    def click_send(self) -> None:
        if not self._click_first(SEND_SELECTORS, timeout_ms=8000):
            raise GoogleVoiceTransportError("Google Voice send control not found")
        try:
            self._page.wait_for_timeout(800)
        except Exception:
            pass

    def close(self) -> None:
        try:
            self._context.close()
        except Exception:
            pass
        try:
            self._playwright.stop()
        except Exception:
            pass

    def _first_visible(self, selectors: Sequence[str], *, timeout_ms: int) -> Optional[Any]:
        for selector in selectors:
            try:
                locator = self._page.locator(selector).first
                locator.wait_for(state="visible", timeout=timeout_ms)
                return locator
            except Exception:
                continue
        return None

    def _click_first(self, selectors: Sequence[str], *, timeout_ms: int) -> bool:
        locator = self._first_visible(selectors, timeout_ms=timeout_ms)
        if locator is None:
            return False
        locator.click()
        return True
