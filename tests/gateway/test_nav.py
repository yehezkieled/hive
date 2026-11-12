"""The top bar is the same on every desk page: the same links, in the same place, at every width."""

from __future__ import annotations

import html as html_mod
import json
import re
import stat
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from hive.gateway import pages
from hive.gateway.actions import Outcome
from hive.gateway.app import create_app
from hive.gateway.settings import GatewaySettings
from tests.gateway.test_stack_home import CHROME, needs_chrome

FIXTURE = Path(__file__).parent.parent / "fixtures" / "gateway" / "fleet-snapshot.v1.json"
OWNER = "owner@example.test"
HOST = "desk.example.ts.net"
GOOD = {"tailscale-user-login": OWNER, "host": HOST}

# Every page in the menu, with the link it marks as current.
MENU_PAGES = {
    "/": "/",
    "/chat": "/chat",
    "/config": "/config",
    "/machine": "/machine",
    "/run": "/run",
    "/terminal": "/terminal",
    "/signins": "/signins",
}


@pytest.fixture(scope="module")
def rendered(tmp_path_factory: pytest.TempPathFactory) -> dict[str, str]:
    """Each menu page and a project page as served, plus the pages answering a form post."""
    home = tmp_path_factory.mktemp("fm")
    (home / "bin").mkdir()
    script = home / "bin" / "fm-fleet-snapshot.sh"
    script.write_text(f"#!/usr/bin/env bash\ncat '{FIXTURE}'\n")
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    settings = GatewaySettings(
        owner_login=OWNER,
        allowed_hosts=(HOST,),
        fm_home=home,
        data_dir=home / "data",
        snapshot_ttl_s=0,
        fleet_up=None,
    )
    client = TestClient(create_app(settings), client=("127.0.0.1", 5000))
    out: dict[str, str] = {}
    for path in (*MENU_PAGES, "/p/alpha"):
        res = client.get(path, headers=GOOD)
        assert res.status_code == 200, path
        out[path] = res.text
    out["result"] = pages.render_result("Sent", "Your note reached firstmate.", "/", True)
    out["outcome"] = pages.render_outcome(Outcome("chat", "-", "noted"), "/chat")
    out["confirm"] = pages.render_confirm(
        "Restart", "Restart the session?", "restart", pages.Ctx("t"), {}
    )
    return out


def _bar(html: str) -> str:
    """The bar from its start to the end of the menu (the chips after it are live state)."""
    m = re.search(r"<header class=chrome>.*?</nav>", html, re.S)
    assert m, "no top bar"
    return m.group(0)


def test_every_page_has_the_same_menu(rendered: dict[str, str]) -> None:
    bars = {page: _bar(html).replace(" aria-current=page", "") for page, html in rendered.items()}
    assert len(set(bars.values())) == 1, bars
    menu = next(iter(bars.values()))
    assert "<a href='/terminal'>Terminal</a>" in menu
    assert "<a href='/signins'>Sign-ins</a>" in menu


def test_each_menu_page_marks_itself_and_only_itself(rendered: dict[str, str]) -> None:
    for page, href in MENU_PAGES.items():
        current = re.findall(r"<a href='([^']*)' aria-current=page>", _bar(rendered[page]))
        assert current == [href], page
    for page in ("/p/alpha", "result", "outcome", "confirm"):
        assert "aria-current" not in _bar(rendered[page]), page


# One Chrome run renders every page in a same-origin frame of each size and measures the bar.
SIZES = [(375, 667), (820, 1180), (1180, 820), (1440, 500)]  # phone, iPad both ways, short PC
# The approved Comb board's bar at each size (docs/design/comb-redesign.html): the content's left
# edge (the page gutter, which the right edge mirrors) and the bar's height. Below 1024 px the
# links take a row of their own under the brand and chips.
GEOMETRY = {"375x667": (16, 101), "820x1180": (24, 101), "1180x820": (32, 57), "1440x500": (80, 57)}
MEASURE = """
<!doctype html><html><body style="margin:0"><script>
var PAGES=__PAGES__,SIZES=__SIZES__,R={};
function box(e){var r=e.getBoundingClientRect();return [r.left,r.top,r.width,r.height];}
Promise.all(Object.keys(PAGES).flatMap(function(name){return SIZES.map(function(s){
  return new Promise(function(done){
    var f=document.createElement('iframe');f.width=s[0];f.height=s[1];
    f.style.cssText='border:0;display:block';
    f.onload=function(){var d=f.contentDocument,h=d.querySelector('header.chrome');
      var links=[].filter.call(h.querySelectorAll('nav a,nav button'),function(a){
        return a.getClientRects().length;});
      (R[s.join('x')]=R[s.join('x')]||{})[name]={bar:box(h),brand:box(h.firstChild),
        stRight:h.querySelector('.chrome__st').getBoundingClientRect().right,
        nav:box(h.querySelector('nav')),
        links:links.map(function(a){return [a.textContent].concat(box(a));}),
        pageScrollsSideways:d.documentElement.scrollWidth>d.documentElement.clientWidth,
        pageScrolls:d.documentElement.scrollHeight>s[1]+1,
        mainScrolls:(function(m){return m.scrollHeight>m.clientHeight+1;})(
          d.querySelector('main'))};
      done();};
    f.srcdoc=PAGES[name];document.body.appendChild(f);});});}))
.then(function(){var o=document.createElement('pre');o.id='result';
  o.textContent=JSON.stringify(R);document.body.appendChild(o);});
</script></body></html>
"""


@pytest.fixture(scope="module")
def measured(
    rendered: dict[str, str], tmp_path_factory: pytest.TempPathFactory
) -> dict[str, dict[str, dict]]:
    """Every rendered page at every size in one headless Chrome run: {size: {page: geometry}}."""
    if not CHROME:
        pytest.skip("no Chrome")
    tmp_path = tmp_path_factory.mktemp("nav-chrome")
    doc = MEASURE.replace("__PAGES__", json.dumps(rendered).replace("</", "<\\/")).replace(
        "__SIZES__", json.dumps(SIZES)
    )
    file = tmp_path / "bars.html"
    file.write_text(doc)
    out = subprocess.run(
        [
            str(CHROME),
            "--headless=new",
            "--no-sandbox",
            "--disable-gpu",
            f"--user-data-dir={tmp_path / 'chrome'}",
            "--window-size=1600,1200",
            "--virtual-time-budget=8000",
            "--dump-dom",
            file.as_uri(),
        ],
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )
    m = re.search(r'<pre id="result">(.*?)</pre>', out.stdout, re.S)
    assert m, out.stdout[-500:]
    return json.loads(html_mod.unescape(m.group(1)))


@needs_chrome
def test_the_bar_does_not_move_between_pages_in_a_real_browser(
    rendered: dict[str, str], measured: dict[str, dict[str, dict]]
) -> None:
    result = measured
    assert sorted(result) == sorted("x".join(map(str, s)) for s in SIZES)
    for size, by_page in result.items():
        assert sorted(by_page) == sorted(rendered), size
        desk = by_page["/"]
        labels = [link[0] for link in desk["links"]]
        assert labels[:7] == ["Desk", "Chat", "Config", "Machine", "Run", "Terminal", "Sign-ins"]
        width = int(size.split("x")[0])
        for link in desk["links"]:  # every link is on screen, none pushed past the edge
            assert 0 <= link[1] and link[1] + link[3] <= width, (size, link)
            assert link[4] >= 44, (size, link)  # a full-height tap target
        gutter, height = GEOMETRY[size]
        assert desk["bar"] == [0, 0, width, height], size
        assert desk["brand"][0] == gutter, size
        assert len({round(link[2]) for link in desk["links"]}) == 1, size  # one row of links
        if width < 1024:  # the links' own row runs under the brand
            assert desk["links"][0][2] >= desk["brand"][1] + desk["brand"][3], size
        for page, geo in by_page.items():
            assert not geo["pageScrollsSideways"], (size, page)
            assert geo["stRight"] == width - gutter, (size, page)  # chips end at the gutter
            for part in ("bar", "brand", "nav", "links"):
                assert geo[part] == desk[part], (size, page, part)


# The pages already on the approved Comb layout: each is one screen at every size, so neither
# the page nor its main area scrolls; long lists scroll inside their own panel.
ONE_SCREEN = ("/", "/chat", "/config", "/machine", "/run", "/signins", "/terminal", "/p/alpha")


@needs_chrome
def test_each_page_is_one_screen_at_every_size(measured: dict[str, dict[str, dict]]) -> None:
    for size, by_page in measured.items():
        for page in ONE_SCREEN:
            geo = by_page[page]
            assert not geo["pageScrolls"] and not geo["mainScrolls"], (size, page)
            assert not geo["pageScrollsSideways"], (size, page)
