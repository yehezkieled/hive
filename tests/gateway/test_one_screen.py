"""Every desk page is one screen, filled with realistic data, at phone, iPad and PC sizes.

Neither the page nor its main area scrolls, and nothing pushes it sideways: a long list scrolls
inside its own panel. On a phone the same holds for every tab under the title.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from hive.gateway import fmconfig, pages
from hive.gateway.chat import ChatView, Receipt
from tests.gateway.test_stack_home import _busy_desk, _in_chrome, needs_chrome

SIZES = {
    "phone": (390, 844),
    "small-phone": (375, 667),
    "ipad-portrait": (820, 1180),
    "ipad-landscape": (1180, 820),
    "pc": (1440, 900),
}


def _chat() -> str:
    receipts = [
        Receipt(
            f"n{i}",
            f"2030-01-02T{i // 6:02d}:{i % 6 * 10:02d}:00Z",
            ("chat", "delegate", "merge", "ticket")[i % 4],
            f"Note {i}: where are we with the energy plans snapshot and the runner?",
            "replied",
            "Built and merged. The daily snapshot is set; it needs the self-hosted runner. " * 2,
            f"2030-01-02T{i // 6:02d}:{i % 6 * 10 + 5:02d}:00Z",
            "",
        )
        for i in range(30)
    ]
    return pages.render_chat(ChatView(True, True, receipts, []), pages.Ctx("t"))


def _config() -> str:
    postures = {
        f"project-{i:02d}": fmconfig.Posture(
            ("no-mistakes-prod-only", "local-only")[i % 2], i % 3 > 0
        )
        for i in range(30)
    }
    rules = tuple(
        fmconfig.Rule(
            f"Rule {i}: the task is genuinely hard or ambiguous planning, research, architecture, "
            "product design, or a diagnosis whose cause is unknown.",
            (fmconfig.Profile("claude", "claude-opus-5-5", "xhigh"),),
        )
        for i in range(12)
    )
    cfg = fmconfig.FmConfig(
        fmconfig.Projects(postures),
        fmconfig.Dispatch(rules, (fmconfig.Profile("claude", "claude-opus-5-5", "medium"),)),
        fmconfig.Value("codex"),
        fmconfig.Value("bypass", absent=True),
    )
    return pages.render_config(cfg, pages.Ctx("t"))


PAGES: dict[str, Callable[[], str]] = {"desk": _busy_desk, "chat": _chat, "config": _config}

DRIVER = """
function st(){var m=q('main'),de=document.documentElement;
var bodies=[].filter.call(document.querySelectorAll('.pn__b'),
function(b){return b.getClientRects().length;});
return {page:de.scrollHeight>innerHeight+1,side:de.scrollWidth>innerWidth+1,
main:m.scrollHeight>m.clientHeight+1,
over:bodies.filter(function(b){return b.scrollHeight>b.clientHeight+1;}).length,
stuck:bodies.filter(function(b){return b.scrollHeight>b.clientHeight+1&&
getComputedStyle(b).overflowY!=='auto';}).length};}
R.views=[st()];
var tabs=[].filter.call(document.querySelectorAll('.tabs--p [data-tab]'),
function(t){return t.getClientRects().length;});
tabs.forEach(function(t){click(t);R.views.push(st());});
"""


@needs_chrome
@pytest.mark.parametrize("size", list(SIZES), ids=list(SIZES))
@pytest.mark.parametrize("page", list(PAGES))
def test_page_is_one_screen(tmp_path: Path, page: str, size: str) -> None:
    r = _in_chrome(tmp_path, PAGES[page](), DRIVER, SIZES[size])
    assert "error" not in r, r
    for v in r["views"]:
        assert not v["page"] and not v["main"] and not v["side"], v
        assert v["stuck"] == 0, v  # a panel too long for its space scrolls on its own
    assert any(v["over"] for v in r["views"])  # the data really was too long for one screen
