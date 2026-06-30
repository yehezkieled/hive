"""Ticket 043 — standalone PWA: iOS status bar overlaps the top bar (040 follow-up).

Regression guard for the verified fix. Two earlier attempts failed on iPad:
  1. `padding-top: env(safe-area-inset-top)` — no-op, because a non-notch iPad
     reports `env(safe-area-inset-top)` as **0** (the safe area goes to the top
     edge; the inset exists on iPhone for the notch "ears", not the status bar).
  2. `status-bar-style: default` instead of `black-translucent` — no effect,
     because `viewport-fit=cover` (Ticket 037) already makes the web view
     full-bleed under the status bar; the style only changes the bar's
     appearance, not whether content sits under it.

The fix that works: reserve a CONSTANT top strip, gated to standalone mode, with
`max(env(safe-area-inset-top), 24px)` — NOT the `env(.., 24px)` fallback form
(the fallback only fires when the value is *undefined*; iPad reports 0, a defined
value, so it never triggers). `max()` floors the reservation at the 24px iPad
status-bar height and still grows to the real notch inset on iPhones.
`status-bar-style: default` is kept for dark icons, visible over the paper strip.

The true acceptance is an on-device iPad standalone re-smoke (portrait +
landscape), which pytest/curl/in-Safari cannot observe.
"""

from __future__ import annotations

import re
from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from hive.web.app import create_app


def _client() -> TestClient:
    pm = MagicMock()
    pm.entities = {}
    return TestClient(create_app(process_manager=pm))


def _html() -> str:
    resp = _client().get("/")
    assert resp.status_code == 200
    return resp.text


def _css() -> str:
    resp = _client().get("/static/landing.css")
    assert resp.status_code == 200
    return resp.text


class TestStandaloneStatusBar:
    """The standalone PWA must not let the iOS status bar overlap the top bar."""

    def test_status_bar_style_is_default(self) -> None:
        # Dark status-bar icons, visible over the light paper strip.
        assert '<meta name="apple-mobile-web-app-status-bar-style" content="default">' in _html()

    def test_top_bar_reserves_fixed_top_strip_in_standalone(self) -> None:
        css = _css()
        # The fix lives behind a standalone-mode media query so the in-Safari tab
        # (where env == 0 is correct) gets no phantom gap.
        assert re.search(r"@media[^{]*\(display-mode:\s*standalone\)", css), (
            "the top-strip reservation must be gated to display-mode: standalone"
        )
        # Load-bearing: the floored form max(env(...), 24px), NOT env(.., 24px).
        assert "max(env(safe-area-inset-top), 24px)" in css, (
            "iPad reports env(safe-area-inset-top) as 0, so the reservation must "
            "floor at 24px via max() — env's fallback arg never fires on iPad"
        )

    def test_not_the_broken_env_fallback_form(self) -> None:
        # env(safe-area-inset-top, 24px) is the common WRONG fix — it does nothing
        # on iPad (the value is 0, not undefined, so the fallback never fires).
        assert "env(safe-area-inset-top, 24px)" not in _css()
