"""Clipboard and browser helpers. They never click 'Post' for you."""
from __future__ import annotations

import webbrowser


def copy_to_clipboard(text: str) -> bool:
    try:
        import pyperclip
        pyperclip.copy(text)
        return True
    except Exception:  # noqa: BLE001 - no clipboard on headless machines; the UI shows the text instead
        return False


def open_url(url: str) -> bool:
    try:
        return bool(webbrowser.open(url))
    except Exception:  # noqa: BLE001
        return False
