"""Ticket 037 — responsive / touch shell (iPad daily driver).

Regression-guard contracts for the drawer-based touch shell (ADR 0022). These
assert the *structure* is present in the rendered landing page + stylesheet — the
true acceptance (real-iPad portrait/landscape re-smoke) cannot run in pytest, so
these lock in the contract and catch regressions (esp. the headline
`.chat-rail{display:none}` bug coming back, and the Enter-to-send hotfix being
clobbered).
"""

from __future__ import annotations

import re
from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from hive.web.app import create_app


def _bare_pm() -> MagicMock:
    pm = MagicMock()
    pm.entities = {}
    return pm


def _client() -> TestClient:
    return TestClient(create_app(process_manager=_bare_pm()))


def _html() -> str:
    resp = _client().get("/")
    assert resp.status_code == 200
    return resp.text


def _css() -> str:
    resp = _client().get("/static/landing.css")
    assert resp.status_code == 200
    return resp.text


class TestDrawerNotHidden:
    """D1 — the headline bug: the chat rail must never be display:none."""

    def test_chat_rail_not_display_none(self) -> None:
        css = _css()
        # The exact bug rule (and whitespace variants) must be gone.
        assert not re.search(r"\.chat-rail\s*\{\s*display:\s*none", css), (
            "chat-rail is hidden — the iPad-portrait command surface vanishes"
        )

    def test_drawer_open_toggle_class(self) -> None:
        css = _css()
        # A class-toggled reveal (.shell.drawer-open … .chat-rail) replaces it.
        assert "drawer-open" in css
        assert "translateX" in css  # the slide-over transform


class TestDrawerMarkup:
    """D1/D8 — toggle, scrim, close-x, and dialog semantics."""

    def test_drawer_toggle_present_with_aria(self) -> None:
        html = _html()
        assert "drawer-toggle" in html
        assert "aria-expanded" in html
        assert "aria-controls" in html

    def test_scrim_and_close_present(self) -> None:
        html = _html()
        assert "drawer-scrim" in html
        assert "drawer-close" in html  # the in-rail close-x

    def test_rail_has_dialog_role(self) -> None:
        html = _html()
        assert 'role="dialog"' in html
        assert "aria-modal" in html


class TestViewportAndSafeArea:
    """D5 — viewport-fit=cover unlocks the safe-area insets."""

    def test_viewport_fit_cover(self) -> None:
        assert "viewport-fit=cover" in _html()

    def test_safe_area_insets_used(self) -> None:
        assert "env(safe-area-inset-" in _css()


class TestTapTargetsAndHover:
    """D3/D4 — coarse-pointer 44px sizing; hover lifts gated to fine pointers."""

    def test_pointer_coarse_block(self) -> None:
        css = _css()
        assert re.search(r"@media\s*\(pointer:\s*coarse\)", css)
        assert "44px" in css  # the touch-target minimum

    def test_hover_hover_gate(self) -> None:
        assert re.search(r"@media\s*\(hover:\s*hover\)", _css())


class TestKeyboardHandling:
    """D6 — visualViewport drives a --kb var; the drawer container shrinks."""

    def test_visualviewport_listener(self) -> None:
        assert "visualViewport" in _html()

    def test_keyboard_custom_property(self) -> None:
        assert "--kb" in _css()
        assert "--kb" in _html()  # the JS writes it


class TestA11yContainment:
    """D8 — inert in both states + an aria-live announcement channel."""

    def test_inert_used(self) -> None:
        assert "inert" in _html()

    def test_aria_live_node(self) -> None:
        assert "aria-live" in _html()

    def test_reduced_motion_block(self) -> None:
        assert "prefers-reduced-motion" in _css()


class TestHotfixPreserved:
    """D7 — the Enter-to-send hotfix (PR #198) must survive byte-for-byte."""

    def test_submit_composer_present(self) -> None:
        html = _html()
        assert "submitComposer" in html
        assert "isComposing" in html  # predictive-text guard
        assert "requestSubmit" in html


class TestPointerdownDismiss:
    """D9 — outside-close/select use pointerdown (iOS doesn't synthesize
    mousedown on tap); the desktop drag-resizer keeps its mouse path."""

    def test_no_document_level_mousedown(self) -> None:
        html = _html()
        # outside-close handlers were document.addEventListener('mousedown', …)
        assert not re.search(r"document\.addEventListener\(\s*['\"]mousedown", html), (
            "a document-level mousedown outside-close remains — fails on iOS tap"
        )

    def test_pointerdown_used(self) -> None:
        assert "pointerdown" in _html()
