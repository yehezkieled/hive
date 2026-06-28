"""Ticket 043 — standalone PWA: iOS status bar overlaps the top bar (040 follow-up).

Regression guard: the shared `.top-bar` rule must reserve the *top* safe-area
inset. In the installed (standalone) PWA, Ticket 040's
`apple-mobile-web-app-status-bar-style: black-translucent` makes iOS draw the web
view *under* a translucent status bar — so without `env(safe-area-inset-top)` of
top reservation, the clock/battery overlap the brand/tabs.

Ticket 037 reserved the LEFT/RIGHT insets (the notch) but never the top; this
locks the top reservation in. The 037 guard `test_safe_area_insets_used` only
checks the inset appears *somewhere* in the sheet (it passes today), so it cannot
catch the top inset being dropped — this scopes the assertion to the `.top-bar`
rule itself.

The TRUE acceptance is an on-device iPad re-smoke in standalone mode (portrait +
landscape), which pytest/curl/in-Safari cannot observe; this catches the CSS
regression of the top reservation disappearing.
"""

from __future__ import annotations

import re
from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from hive.web.app import create_app


def _css() -> str:
    pm = MagicMock()
    pm.entities = {}
    resp = TestClient(create_app(process_manager=pm)).get("/static/landing.css")
    assert resp.status_code == 200
    return resp.text


def _top_bar_rule(css: str) -> str:
    """Body of the primary `.top-bar { ... }` rule (not `.top-bar__*` children).

    `re.search` returns the first match, which is the base rule near the top of
    the sheet — ahead of any `@media` override like `.top-bar { z-index: … }`.
    """
    match = re.search(r"\.top-bar\s*\{([^}]*)\}", css)
    assert match, ".top-bar rule not found in landing.css"
    return match.group(1)


class TestTopBarClearsStatusBar:
    """The standalone status bar must not overlap the top bar (043)."""

    def test_top_bar_reserves_top_safe_area_inset(self) -> None:
        rule = _top_bar_rule(_css())
        assert "env(safe-area-inset-top)" in rule, (
            ".top-bar must reserve env(safe-area-inset-top); without it the iOS "
            "status bar overlaps the brand/tabs in the installed standalone PWA"
        )
