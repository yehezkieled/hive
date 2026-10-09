"""The desk is one screen at every size: the delegate bar is flush with the viewport bottom.

There is no browser harness, so these pin the CSS rules that guarantee it (verified in a real
browser at 2000x1378, 1378x2000, 820x1180 and 390x844: bar bottom == viewport bottom)."""

from __future__ import annotations

import re

from hive.gateway import pages


def _rule(selector: str) -> str:
    bodies = re.findall(re.escape(selector) + r"\{([^}]*)\}", pages.CSS)
    assert bodies, selector
    return ";".join(bodies)


def test_desk_body_is_viewport_height_with_no_bottom_padding() -> None:
    body = _rule("body.wide")
    assert "height:100vh;height:var(--app-h,100dvh)" in body
    assert "overflow:hidden" in body and "padding-bottom:0" in body


def test_desk_main_has_no_trailing_padding_under_the_bar() -> None:
    assert "padding:8px 0 0" in _rule(".wide main")


def test_desk_bar_is_the_last_flex_row_not_sticky_or_offset() -> None:
    bar = _rule(".wide .dbar-wrap")
    assert "position:static" in bar and "flex:none" in bar and "margin:0" in bar
    assert "env(safe-area-inset-bottom)" in bar
    assert "bottom:12px" not in pages.CSS


def test_desk_content_scrolls_not_the_page() -> None:
    assert "overflow-y:auto" in _rule(".wide .land")
