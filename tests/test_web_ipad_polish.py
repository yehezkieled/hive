"""Tests for Ticket 042 — iPad web polish & token-entry UX.

Locks the contract from `docs/tickets/042-ipad-web-polish/design.md`:
- D1: the web token persists in ``localStorage`` (not ``sessionStorage``), so the
  iPad prompts once per device, not once per tab. Modal copy matches.
- D2: the dead ``+ New`` / ``History`` header buttons are removed.
- D3: the service worker serves ``landing.css`` network-first (a dedicated branch)
  and ``CACHE_VERSION`` is bumped so existing installs flush.

D4 (the keyboard-up composer safe-area gap) is verification-gated on a real iPad
and has no unit test — see the ticket's design.md / plan.md.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from hive.web.app import STATIC_DIR, TEMPLATES_DIR, create_app


def _bare_pm() -> MagicMock:
    """ProcessManager mock with no entities registered."""
    pm = MagicMock()
    pm.entities = {}
    return pm


def _client() -> TestClient:
    return TestClient(create_app(process_manager=_bare_pm()))


class TestTokenPersistence:
    """D1 — the token survives a tab close (localStorage), not sessionStorage."""

    def test_landing_template_has_no_sessionstorage(self) -> None:
        # Source-level guard: catches every token site, including ones inside
        # Jinja conditionals that a bare-PM render would omit.
        src = (TEMPLATES_DIR / "landing.html").read_text()
        assert "sessionStorage" not in src
        assert "localStorage.getItem('hive_web_token')" in src

    def test_refresh_js_has_no_sessionstorage(self) -> None:
        # Includes the doc comment at the top of the file, not just the code.
        src = (STATIC_DIR / "dashboard" / "refresh.js").read_text()
        assert "sessionStorage" not in src
        assert "localStorage.getItem('hive_web_token')" in src

    def test_served_landing_uses_localstorage(self) -> None:
        html = _client().get("/").text
        assert "localStorage.getItem('hive_web_token')" in html
        assert "sessionStorage" not in html

    def test_modal_hint_says_device_not_tab(self) -> None:
        html = _client().get("/").text
        assert "Stored on this device" in html
        assert "tab only" not in html


class TestDeadButtonsRemoved:
    """D2 — the placeholder header buttons (no handlers) are gone."""

    def test_no_history_or_new_buttons_in_html(self) -> None:
        html = _client().get("/").text
        assert "chat-rail__head-actions" not in html
        assert ">History<" not in html
        assert "+ New" not in html

    def test_orphan_head_actions_css_removed(self) -> None:
        css = _client().get("/static/landing.css").text
        assert "chat-rail__head-actions" not in css


class TestServiceWorkerCache:
    """D3 — landing.css network-first + cache bump, offline shell intact."""

    def test_cache_version_bumped(self) -> None:
        body = _client().get("/service-worker.js").text
        # v5: bumped in 043 (standalone status-bar reservation); 041 added
        # push/notificationclick handlers (behaviour, not assets) at the same version.
        assert "hive-v5" in body
        assert "hive-v4" not in body
        assert "hive-v2" not in body

    def test_landing_css_has_dedicated_network_first_branch(self) -> None:
        body = _client().get("/service-worker.js").text
        # A branch keyed on the exact path → it's handled before the generic
        # /static/* stale-while-revalidate block can catch it.
        assert "url.pathname === '/static/landing.css'" in body
        assert "network-first" in body.lower()

    def test_offline_shell_still_precached(self) -> None:
        body = _client().get("/service-worker.js").text
        assert "/static/offline.html" in body
