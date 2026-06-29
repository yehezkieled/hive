"""Ticket 043 — standalone PWA: iOS status bar overlaps the top bar (040 follow-up).

Regression guard for the chosen fix (approach A): the installed PWA uses
`apple-mobile-web-app-status-bar-style: default`, so iOS reserves the status-bar
strip and starts the web view *below* it.

The first attempt kept `black-translucent` (web view drawn *under* a translucent
status bar) and reserved `env(safe-area-inset-top)` of top padding on `.top-bar`.
That is correct on iPhone but fails on iPad: with no notch, iPadOS reports
`env(safe-area-inset-top)` as 0 in standalone, so the reservation computed to
zero and the clock kept overlapping the brand. `default` sidesteps the inset
entirely — the OS owns the spacing.

This guards against a regression back to `black-translucent`. The true acceptance
is an on-device iPad standalone re-smoke (portrait + landscape), which
pytest/curl/in-Safari cannot observe.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from hive.web.app import create_app


def _html() -> str:
    pm = MagicMock()
    pm.entities = {}
    resp = TestClient(create_app(process_manager=pm)).get("/")
    assert resp.status_code == 200
    return resp.text


class TestStandaloneStatusBarStyle:
    """The PWA must not draw under the iOS status bar (043 / approach A)."""

    def test_status_bar_style_is_default(self) -> None:
        html = _html()
        assert '<meta name="apple-mobile-web-app-status-bar-style" content="default">' in html, (
            "PWA must use status-bar-style=default so iOS reserves the status-bar "
            "strip; black-translucent overlaps the top bar on iPad (inset == 0)"
        )

    def test_not_black_translucent(self) -> None:
        assert "black-translucent" not in _html(), (
            "black-translucent reintroduces the iPad standalone status-bar overlap "
            "(043) — iPad reports env(safe-area-inset-top) as 0"
        )
