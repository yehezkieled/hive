"""The Sign-ins page: each sign-in's state, and renewing one with the vendor's own login command.

Every login command here is a fake script in a temp directory. No vendor is contacted and
no real sign-in or credentials file is touched.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from urllib.parse import urlencode

import pytest
from fastapi.testclient import TestClient

from hive.gateway import machine, pages, signins
from hive.gateway.actions import ActionError
from hive.gateway.app import _safe_next, create_app
from hive.gateway.live import LiveHub
from hive.gateway.machine import Reading, Unknown
from hive.gateway.settings import GatewaySettings
from hive.gateway.signins import SignInService
from tests.gateway.test_actions import CSRF, GOOD, HOST, OWNER, home  # noqa: F401
from tests.gateway.test_machine import (
    CLAUDE_EXPIRY_MS,
    DAY,
    LOGIN,
    NOW,
    SECRET,
    SIZES,
    _client,
    _exe,
    _main,
    credentials,
    tailscale_json,
)
from tests.gateway.test_stack_home import FAKE_LIVE, _in_chrome, needs_chrome

LINK = (
    "https://claude.com/cai/oauth/authorize?code=true&client_id=PLANTEDlinkPLANTED"
    "&response_type=code&redirect_uri=https%3A%2F%2Fplatform.claude.com%2Foauth%2Fcode%2Fcallback"
    "&scope=user%3Ainference&code_challenge=PLANTEDlinkPLANTEDchallenge&state=PLANTEDlinkPLANTED"
)
PASTED = "PLANTEDcodePLANTED#PLANTEDstatePLANTED"
DEVICE = "https://github.com/login/device"
ONE_TIME = "AB12-CD34"
NEW_EXPIRY_MS = CLAUDE_EXPIRY_MS + 180 * 86400 * 1000
RID = "web-00000000000000a1"
JSON = {**GOOD, "accept": "application/json"}

# ``claude auth login`` as Claude Code 2.1.322 prints it: the link as a terminal hyperlink,
# then a prompt that reads lines until one holds "code#state".
FAKE_CLAUDE = r"""
printf '%s\n' "$@" > "$0.argv"; echo $$ > "$0.pid"
printf 'Opening browser to sign in…\n'
printf "If the browser didn't open, visit: \033]8;;%s\007%s\033]8;;\007\n" '@LINK@' '@LINK@'
printf 'Paste code here if prompted > '
while IFS= read -r code; do
  printf '%s\n' "$code" >> "$0.got"
  printf 'you typed %s\n' "$code"
  case "$code" in
    *'#'*) printf '%s' '@CREDS@' > "$0.credentials"; printf 'Login successful.\n'; exit 0 ;;
    *) printf 'Invalid code. Please make sure the full code was copied.\n' >&2 ;;
  esac
done
exit 1
"""
# ``gh``: the status read, and the device flow that ends when "$0.done" appears.
FAKE_GH = r"""
if [ "$2" = status ]; then cat "$0.status" 2>/dev/null || echo '{"hosts":{}}'; exit 0; fi
printf '%s\n' "$@" > "$0.argv"; echo $$ > "$0.pid"
printf '\033[33m!\033[0m First copy your one-time code: \033[1m@CODE@\033[0m\n'
@PROMPT@
while [ ! -e "$0.done" ]; do sleep 0.05; done
printf '\033[32m✓\033[0m Authentication complete.\n'
printf '%s' '@STATUS@' > "$0.status"
printf '\033[32m✓\033[0m Logged in as someone\n'
"""
GH_OPEN = "printf '\\033[1mOpen this URL\\033[0m to continue in your web browser: %s\\n' '@DEVICE@'"
GH_ENTER = (
    "printf '\\033[1mPress Enter\\033[0m to open %s in your browser... ' '@DEVICE@'; "
    'IFS= read -r _; echo entered > "$0.entered"'
)


def gh_status(state: str = "success", source: str = "keyring", **more: object) -> str:
    entry = {
        "active": True,
        "host": "github.com",
        "login": LOGIN,
        "scopes": "gist, read:org, repo",
        "state": state,
        "tokenSource": source,
        "gitProtocol": "https",
        **more,
    }
    return json.dumps({"hosts": {"github.com": [entry]}})


class Fakes:
    """Fake login commands and the files they read, outside the desk's own directories."""

    def __init__(self, root: Path) -> None:
        self.dir = root / "fakes"
        self.dir.mkdir()
        self.claude = self.dir / "claude"
        self.gh = self.dir / "gh"
        self.tailscale = _exe(self.dir / "tailscale", f"cat '{self.dir}/tailscale.json'")
        self.creds = self.dir / "claude.credentials"
        self.creds.write_text(credentials())
        (self.dir / "tailscale.json").write_text(tailscale_json())
        (self.dir / "gh.status").write_text(gh_status())
        self.set_claude(FAKE_CLAUDE)
        self.set_gh()

    def set_claude(self, body: str) -> None:
        body = body.replace("@LINK@", LINK).replace("@CREDS@", credentials(NEW_EXPIRY_MS))
        _exe(self.claude, body)

    def set_gh(self, prompt: str = GH_OPEN, code: str = ONE_TIME, body: str = FAKE_GH) -> None:
        body = body.replace("@PROMPT@", prompt).replace("@CODE@", code)
        _exe(self.gh, body.replace("@DEVICE@", DEVICE).replace("@STATUS@", gh_status()))

    def settings(self, root: Path, fm_home: Path | None = None, **changes: object):
        base: dict = {
            "owner_login": OWNER,
            "allowed_hosts": (HOST,),
            "fm_home": fm_home or root / "fm",
            "snapshot_ttl_s": 60,
            "data_dir": root / "data",
            "live": False,
            "claude": self.claude,
            "gh": self.gh,
            "tailscale": self.tailscale,
            "claude_credentials": self.creds,
        }
        return GatewaySettings(**{**base, **changes})

    def alive(self, name: str) -> bool:
        try:
            os.kill(int((self.dir / f"{name}.pid").read_text()), 0)
        except (ProcessLookupError, FileNotFoundError, ValueError):
            return False
        return True

    def read(self, name: str) -> str:
        path = self.dir / name
        return path.read_text() if path.exists() else ""


@pytest.fixture
def fakes(tmp_path: Path) -> Fakes:
    return Fakes(tmp_path)


Make = Callable[..., SignInService]


@pytest.fixture
async def make(fakes: Fakes, tmp_path: Path) -> AsyncIterator[Make]:
    """A service over the fake commands; every one made is stopped when the test ends."""
    made: list[SignInService] = []

    def build(settings: GatewaySettings | None = None, **limits: float) -> SignInService:
        made.append(SignInService(settings or fakes.settings(tmp_path), **limits))  # type: ignore[arg-type]
        return made[-1]

    yield build
    for service in made:
        await service.stop()


async def until(service: SignInService, token: str, phase: str, tries: int = 300):
    for _ in range(tries):
        view = service.view(token)
        if view.phase == phase:
            return view
        await asyncio.sleep(0.02)
    raise AssertionError(f"never reached {phase}: {service.view(token)}")


async def gone(fakes: Fakes, name: str, tries: int = 200) -> bool:
    for _ in range(tries):
        if not fakes.alive(name):
            return True
        await asyncio.sleep(0.02)
    return False


def rid(n: int) -> str:
    return f"web-{n:016x}"


# ---- reading a login command's output ------------------------------------------------------

CLAUDE_OUT = (
    "Opening browser to sign in…\r\n"
    f"If the browser didn't open, visit: \x1b]8;;{LINK}\x07{LINK}\x1b]8;;\x07\r\n"
    "Paste code here if prompted > "
)


@pytest.mark.parametrize(
    "out",
    [
        CLAUDE_OUT,
        f"Opening browser to sign in…\nIf the browser didn't open, visit: {LINK}\n",
        # no way back to the PC's browser: the other wording Claude Code prints
        "The browser can't return the sign-in to Claude Code automatically. After you sign in, "
        f"paste the code shown.\nSign in at: {LINK}\nPaste code here > ",
        f"\x1b[2K\x1b[1G\x1b[36mvisit:\x1b[0m \x1b[4m{LINK}\x1b[24m \x1b[?25h",
        # only the hyperlink target carries the link; its label is a word
        f"visit: \x1b]8;;{LINK}\x1b\\this link\x1b]8;;\x1b\\\n",
    ],
)
def test_the_claude_link_is_read_from_what_the_command_prints(out: str) -> None:
    assert signins.parse_claude(*signins.clean(out)) == signins.Parsed(link=LINK)


@pytest.mark.parametrize(
    "out",
    [
        "",
        "\x00\xff\x1b[31m\x07 not a link at all \x1b]0;title\x07\n",
        os.urandom(4096).decode("utf-8", "replace"),
        "visit: http://claude.com/cai/oauth/authorize?x=1\n",  # not https
        "visit: https://evil.example/oauth/authorize?x=1\n",
        "visit: https://claude.com.evil.example/oauth/authorize?x=1\n",  # a look-alike host
        "visit: https://claude.com@evil.example/oauth/authorize?x=1\n",  # a name before the host
        "visit: https://evil.example/?next=https://claude.com/oauth/authorize\n",
        "visit: https://claude.com:8443/cai/oauth/authorize?x=1\n",  # an odd port
        "Read https://docs.claude.com/en/docs/claude-code/setup first.\n",  # not a sign-in page
        "visit: javascript:alert(1)//claude.com/oauth/\n",
        f"visit: {LINK[:-20]}",  # still arriving: no white space after it yet
        f"visit: \x1b]8;;{LINK}",  # a hyperlink sequence still arriving
        "visit: https://claude.com/cai/oauth/authorize?x=" + "a" * 3000 + "\n",  # absurdly long
        'visit: https://claude.com/cai/oauth/authorize?x="><script>alert(1)</script>\n',
    ],
)
def test_claude_output_that_is_not_a_sign_in_link_offers_nothing(out: str) -> None:
    assert signins.parse_claude(*signins.clean(out)) == signins.Parsed()


@pytest.mark.parametrize(
    ("out", "enter"),
    [
        (
            f"\x1b[33m!\x1b[0m First copy your one-time code: \x1b[1m{ONE_TIME}\x1b[0m\n"
            f"\x1b[1mOpen this URL\x1b[0m to continue in your web browser: {DEVICE}\n",
            False,
        ),
        (
            f"! First copy your one-time code: {ONE_TIME}\r\n"
            f"Press Enter to open {DEVICE} in your browser... ",
            True,
        ),
    ],
)
def test_the_github_page_and_one_time_code_are_read(out: str, enter: bool) -> None:
    assert signins.parse_github(*signins.clean(out)) == signins.Parsed(DEVICE, ONE_TIME, enter)


@pytest.mark.parametrize(
    "out",
    [
        "",
        os.urandom(2048).decode("utf-8", "replace"),
        "! First copy your one-time code: not-a-code\nOpen this URL: https://evil.example/login/device\n",
        "Open this URL to continue in your web browser: https://gist.github.com/login/device\n",
        "Open this URL to continue in your web browser: https://github.com/evil/repo\n",
        f"! First copy your one-time code: {ONE_TIME}",  # the code is still arriving
    ],
)
def test_github_output_that_cannot_be_read_offers_nothing(out: str) -> None:
    parsed = signins.parse_github(*signins.clean(out))
    assert parsed.link is None and parsed.code is None


def test_colour_and_cursor_sequences_are_dropped_and_half_ones_are_not_read_past() -> None:
    text, targets = signins.clean(
        "\x1b[1;32mgo\x1b[0m \x1b(Bto\r\nA\rB\x08\x1b]8;;https://x.example/\x07x"
    )
    assert text == "go to\nA\nBx" and targets == ("https://x.example/",)
    assert signins.clean("before \x1b[3")[0] == "before "
    assert signins.clean("before \x1b]8;;https://half")[0] == "before "


@pytest.mark.parametrize(
    ("url", "ok"),
    [
        ("https://claude.ai/oauth/authorize?a=1", True),
        ("https://platform.claude.com/oauth/authorize?a=1", True),
        ("https://console.anthropic.com/oauth/authorize", True),
        ("https://claude.com:443/cai/oauth/authorize", True),
        ("https://notclaude.com/oauth/authorize", False),
        ("https://claude.com/cai/authorize", False),
        ("https://claude.com/oauth/a b", False),
        ("ftp://claude.com/oauth/x", False),
        ("https://claude.com:99999/oauth/x", False),
        ("https://[claude.com/oauth/x", False),
    ],
)
def test_only_an_https_link_on_the_vendors_own_domain_is_accepted(url: str, ok: bool) -> None:
    got = signins.safe_link(url, signins.CLAUDE_DOMAINS, path="/oauth/")
    assert got == (url if ok else None)


# ---- the panel's readings ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("out", "want"),
    [
        (gh_status(), signins.GitHub("success", False)),
        (gh_status("error"), signins.GitHub("error", False)),
        (gh_status("timeout"), signins.GitHub("timeout", False)),
        (gh_status(source="GH_TOKEN"), signins.GitHub("success", True)),
        ('{"hosts":{}}', signins.GitHub("none", False)),
        ('{"hosts":{"github.com":[]}}', signins.GitHub("none", False)),
        ('{"hosts":{"github.example":[{"state":"success"}]}}', signins.GitHub("none", False)),
        (gh_status(state=SECRET), Unknown(machine.NOT_UNDERSTOOD)),
        ('{"hosts":{"github.com":["x"]}}', Unknown(machine.NOT_UNDERSTOOD)),
        ('{"hosts":[]}', Unknown(machine.NOT_UNDERSTOOD)),
        ("not json", Unknown(machine.NOT_UNDERSTOOD)),
        ("[]", Unknown(machine.NOT_UNDERSTOOD)),
    ],
)
def test_github_state_is_one_word_and_nothing_else(out: str, want: object) -> None:
    got = signins.parse_github_status(out)
    assert got == want
    assert LOGIN not in repr(got) and "repo" not in repr(got)


def _states(
    claude: object = NOW + 10 * DAY,
    github: object = None,
    tailscale: object = None,
    at: float = NOW,
) -> signins.States:
    ts = tailscale if tailscale is not None else machine.parse_tailscale(tailscale_json())
    gh = github if github is not None else signins.GitHub("success", False)
    return signins.States(Reading(claude, at), Reading(gh, at), Reading(ts, at), at)


def _plain(tmp_path: Path, **changes: object) -> GatewaySettings:
    base: dict = {
        "data_dir": tmp_path / "data",
        "claude": Path("/bin/true"),
        "gh": Path("/bin/true"),
    }
    return GatewaySettings(**{**base, **changes})


def test_rows_show_state_and_expiry_and_whether_renew_is_offered(tmp_path: Path) -> None:
    claude, github, tailscale = signins.build_rows(_states(), NOW, _plain(tmp_path))
    assert (claude.label, claude.state, claude.headline) == (
        "Claude",
        "warn",
        "Valid, 10 days left",
    )
    assert claude.moment == machine.Moment("expires", NOW + 10 * DAY, exact=True)
    assert claude.renewable and not claude.why_not
    assert (github.state, github.headline, github.renewable) == ("ok", "Signed in", True)
    assert github.moment is None and "no end date" in github.detail
    assert (tailscale.state, tailscale.headline) == ("ok", "Connected")
    assert tailscale.moment is not None and tailscale.moment.label == "key expires"
    assert not tailscale.renewable and tailscale.why_not == signins.TAILSCALE_WHY
    assert tailscale.help_url == "https://login.tailscale.com/admin/machines"


def test_tailscale_is_never_renewed_from_the_desk() -> None:
    """Signing this PC's Tailscale in again would cut the link the desk is reached over."""
    assert set(signins.PROVIDERS) == {"claude", "github"}
    for key in ("NeedsLogin", "Running", "Stopped"):
        ts = machine.Tailscale(key, True, NOW - DAY, key == "NeedsLogin", ())
        row = signins.tailscale_signin(Reading(ts, NOW), NOW)
        assert not row.renewable and row.why_not
    never = machine.Tailscale("Running", True, None, False, ())
    row = signins.tailscale_signin(Reading(never, NOW), NOW)
    assert row.why_not == signins.TAILSCALE_NO_EXPIRY and not row.help_url


@pytest.mark.parametrize(
    ("claude", "state", "headline", "urgent"),
    [
        (NOW + 60 * DAY, "ok", "Valid, 60 days left", False),
        (NOW + 2 * DAY, "warn", "Valid, 2 days left", True),
        (NOW - DAY, "down", "Expired", True),
        (Unknown("its record was not found"), "unknown", "Unknown", False),
    ],
)
def test_the_claude_row(
    tmp_path: Path, claude: object, state: str, headline: str, urgent: bool
) -> None:
    row = signins.build_rows(_states(claude), NOW, _plain(tmp_path))[0]
    assert (row.state, row.headline, row.urgent) == (state, headline, urgent)
    assert row.renewable  # Renew is offered whenever the login command exists
    if state == "unknown":
        assert row.detail == "Its record was not found"


@pytest.mark.parametrize(
    ("github", "state", "headline", "renewable"),
    [
        (signins.GitHub("success", False), "ok", "Signed in", True),
        (signins.GitHub("none", False), "down", "Not signed in", True),
        (signins.GitHub("error", False), "down", "Not working", True),
        (signins.GitHub("timeout", False), "unknown", "Unknown", True),
        (signins.GitHub("odd", False), "unknown", "Unknown", True),
        (signins.GitHub("success", True), "ok", "Signed in", False),  # a token in the environment
        (Unknown(machine.TIMED_OUT), "unknown", "Unknown", True),
    ],
)
def test_the_github_row(
    tmp_path: Path, github: object, state: str, headline: str, renewable: bool
) -> None:
    row = signins.build_rows(_states(github=github), NOW, _plain(tmp_path))[1]
    assert (row.state, row.headline, row.renewable) == (state, headline, renewable)
    assert bool(row.why_not) is not renewable


def test_no_renew_without_the_login_command(tmp_path: Path) -> None:
    claude, github, _ = signins.build_rows(_states(), NOW, _plain(tmp_path, claude=None, gh=None))
    assert not claude.renewable and "was not found" in claude.why_not
    assert not github.renewable and "was not found" in github.why_not


def test_rows_are_unknown_until_read_and_when_the_readings_are_old(tmp_path: Path) -> None:
    for states, words in (
        (None, "Not checked yet"),
        (_states(at=NOW - 600), "Reading it again now"),
    ):
        rows = signins.build_rows(states, NOW, _plain(tmp_path))
        assert [r.state for r in rows] == ["unknown"] * 3
        assert all(r.detail == words and not r.renewable for r in rows)


async def test_the_readings_come_from_the_date_field_and_the_status_commands(
    make: Make, fakes: Fakes, tmp_path: Path
) -> None:
    service = make()
    panel = await service.panel(wait_s=5)
    claude, github, tailscale = panel.rows
    assert claude.moment is not None and claude.moment.at == CLAUDE_EXPIRY_MS / 1000
    assert (github.state, tailscale.state) == ("ok", "ok")
    text = repr(panel) + pages.render_signins(panel, _ctx())
    # nothing of the credentials file, the account names or Tailscale's own link
    assert "PLANTED" not in text and LOGIN not in text and "someone" not in text
    assert not fakes.read("gh.argv")  # reading the state never starts a login


# ---- the flow -----------------------------------------------------------------------------


async def test_claude_shows_the_link_takes_the_code_and_reports_the_new_expiry(
    make: Make, fakes: Fakes
) -> None:
    service = make()
    started = await service.start("claude", RID, OWNER)
    assert started.mine and started.token and started.phase in ("starting", "waiting")
    token = started.token
    waiting = await until(service, token, "waiting")
    assert waiting.link == LINK and waiting.code is None and waiting.takes_code
    assert waiting.ends_at == pytest.approx(time.time() + signins.FLOW_TIMEOUT_S, abs=5)

    assert (await service.send_code(token, f"  {PASTED}\n")).phase == "checking"
    done = await until(service, token, "ok")
    assert done.message == "Claude is signed in." and done.expires == NEW_EXPIRY_MS / 1000
    assert done.link is None and done.code is None and not done.token
    assert fakes.read("claude.got") == PASTED + "\n"  # the code reached the command, trimmed
    assert not fakes.alive("claude")
    # the panel's row moved with it
    row = (await service.panel()).rows[0]
    assert row.moment is not None and row.moment.at == NEW_EXPIRY_MS / 1000


async def test_nothing_from_a_request_reaches_the_command_line(make: Make, fakes: Fakes) -> None:
    service = make()
    token = (await service.start("claude", RID, OWNER)).token
    await until(service, token, "waiting")
    assert fakes.read("claude.argv").split() == ["auth", "login"]
    with pytest.raises(ActionError) as err:
        await service.start("claude; touch /tmp/x", rid(2), OWNER)
    assert err.value.status == 404
    await service.cancel(token, OWNER)
    token = (await service.start("github", rid(3), OWNER)).token
    await until(service, token, "waiting")
    assert fakes.read("gh.argv").split() == ["auth", "login", "--hostname", "github.com", "--web"]


async def test_a_code_claude_turns_down_is_asked_for_again(make: Make, fakes: Fakes) -> None:
    service = make()
    token = (await service.start("claude", RID, OWNER)).token
    await until(service, token, "waiting")
    await service.send_code(token, "half-a-code")
    again = await until(service, token, "waiting")
    assert again.note == "That code was not accepted. Paste the whole code again."
    assert again.link == LINK and again.takes_code
    await service.send_code(token, PASTED)
    assert (await until(service, token, "ok")).note == ""


@pytest.mark.parametrize(
    "code",
    ["", "   ", "two words#x", "abc\x03#x", "abc\x1b[A#x", "a\nb#x", "ü#x", "a" * 600 + "#x"],
)
async def test_a_code_must_be_visible_characters_and_nothing_else_reaches_the_terminal(
    make: Make, fakes: Fakes, code: str
) -> None:
    service = make()
    token = (await service.start("claude", RID, OWNER)).token
    await until(service, token, "waiting")
    with pytest.raises(ActionError) as err:
        await service.send_code(token, code)
    assert err.value.status == 400
    # a fixed sentence: nothing of what was typed comes back
    assert str(err.value) == (
        "That does not look like a sign-in code. Paste it exactly as shown, with no spaces."
    )
    await asyncio.sleep(0.2)
    assert not fakes.read("claude.got") and service.view(token).phase == "waiting"


async def test_github_shows_the_page_and_code_and_finishes_by_itself(
    make: Make, fakes: Fakes
) -> None:
    (fakes.dir / "gh.status").write_text(gh_status("error"))
    service = make()
    token = (await service.start("github", RID, OWNER)).token
    waiting = await until(service, token, "waiting")
    assert (waiting.link, waiting.code, waiting.takes_code) == (DEVICE, ONE_TIME, False)
    with pytest.raises(ActionError, match="does not take a code"):
        await service.send_code(token, "anything")
    (fakes.dir / "gh.done").write_text("")  # the owner finished at GitHub
    done = await until(service, token, "ok")
    assert done.message == "GitHub is signed in." and done.expires is None
    assert not fakes.read("gh.entered")


async def test_gh_waiting_for_enter_is_given_it(make: Make, fakes: Fakes) -> None:
    fakes.set_gh(GH_ENTER)
    service = make()
    token = (await service.start("github", RID, OWNER)).token
    waiting = await until(service, token, "waiting")
    assert (waiting.link, waiting.code) == (DEVICE, ONE_TIME)
    for _ in range(100):
        if fakes.read("gh.entered"):
            break
        await asyncio.sleep(0.02)
    assert fakes.read("gh.entered")


async def test_only_one_sign_in_runs_at_a_time(make: Make, fakes: Fakes) -> None:
    service = make()
    token = (await service.start("claude", RID, OWNER)).token
    for key in ("claude", "github"):
        with pytest.raises(ActionError, match="already running") as err:
            await service.start(key, rid(2), OWNER)
        assert err.value.status == 409
    assert not fakes.read("gh.argv")
    await service.cancel(token, OWNER)
    assert (await service.start("github", rid(3), OWNER)).mine  # free again once it ended


async def test_two_starts_at_once_run_one_command(make: Make, fakes: Fakes) -> None:
    service = make()
    results = await asyncio.gather(
        service.start("claude", rid(1), OWNER),
        service.start("github", rid(2), OWNER),
        return_exceptions=True,
    )
    assert sorted(type(r).__name__ for r in results) == ["ActionError", "FlowView"]


async def test_a_request_id_starts_one_flow_only(make: Make) -> None:
    service = make()
    token = (await service.start("claude", RID, OWNER)).token
    await service.cancel(token, OWNER)
    with pytest.raises(ActionError, match="already used"):
        await service.start("claude", RID, OWNER)


async def test_the_flow_times_out_and_its_command_is_killed(make: Make, fakes: Fakes) -> None:
    service = make(timeout_s=0.6)
    token = (await service.start("claude", RID, OWNER)).token
    assert (await until(service, token, "waiting")).link == LINK
    assert fakes.alive("claude")
    ended = await until(service, token, "failed")
    assert "was not finished in under a minute, so it was stopped" in ended.message
    assert ended.link is None and not ended.takes_code
    assert await gone(fakes, "claude")
    assert "event=failed reason=timed-out" in service.audit_path.read_text()


async def test_cancel_stops_the_command_and_forgets_the_link(make: Make, fakes: Fakes) -> None:
    service = make()
    token = (await service.start("claude", RID, OWNER)).token
    await until(service, token, "waiting")
    after = await service.cancel(token, OWNER)
    assert after.phase in ("stopping", "cancelled") and after.link is None
    ended = await until(service, token, "cancelled")
    assert ended.message == "The sign-in was cancelled." and await gone(fakes, "claude")
    with pytest.raises(ActionError, match="No sign-in is running"):
        await service.send_code(token, PASTED)
    assert (await service.cancel(token, OWNER)).phase == "cancelled"  # asking twice is harmless


async def test_a_command_that_ignores_being_asked_to_stop_is_killed(
    make: Make, fakes: Fakes, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(signins, "KILL_GRACE_S", 0.3)
    fakes.set_claude("trap '' TERM\n" + FAKE_CLAUDE)
    service = make()
    token = (await service.start("claude", RID, OWNER)).token
    await until(service, token, "waiting")
    assert (await service.cancel(token, OWNER)).link is None
    await until(service, token, "cancelled")
    assert await gone(fakes, "claude")


GARBAGE = r"""
echo $$ > "$0.pid"
head -c 3000 /dev/urandom
printf '\nvisit: http://claude.com/cai/oauth/authorize?x=1\n'
printf 'or https://claude.com.evil.example/oauth/authorize?PLANTEDlinkPLANTED=1 \n'
sleep 30
"""


async def test_output_that_cannot_be_read_offers_nothing_and_stops(
    make: Make, fakes: Fakes
) -> None:
    fakes.set_claude(GARBAGE)
    service = make(read_window_s=0.5)
    token = (await service.start("claude", RID, OWNER)).token
    seen = []
    for _ in range(200):
        view = service.view(token)
        seen.append(view)
        if not view.active:
            break
        await asyncio.sleep(0.02)
    assert all(v.link is None and v.code is None and not v.takes_code for v in seen)
    assert seen[-1].phase == "failed" and seen[-1].message == signins.REASONS["not-understood"]
    assert "was not understood, so no link is offered" in seen[-1].message
    assert await gone(fakes, "claude")


@pytest.mark.parametrize(
    ("body", "reason"),
    [
        ("echo 'Login failed: token exchange was refused' >&2; exit 1", "refused"),
        ("echo boom >&2; exit 3", "exited"),
        ("exit 0", "unconfirmed"),  # it said yes, but the sign-in still reads as expired
    ],
)
async def test_a_command_that_fails_gives_a_fixed_plain_reason(
    make: Make, fakes: Fakes, body: str, reason: str
) -> None:
    fakes.creds.write_text(credentials(int((time.time() - DAY) * 1000)))
    fakes.set_claude(body)
    service = make()
    token = (await service.start("claude", RID, OWNER)).token
    ended = await until(service, token, "failed")
    assert ended.message == signins.REASONS[reason] and "boom" not in ended.message
    assert f"event=failed reason={reason}" in service.audit_path.read_text()


async def test_github_using_a_token_from_the_environment_says_so(make: Make, fakes: Fakes) -> None:
    fakes.set_gh(
        body='if [ "$2" = status ]; then echo \'{"hosts":{}}\'; exit 0; fi\n'
        "echo 'The value of the GH_TOKEN environment variable is being used for authentication.'\n"
        "exit 1"
    )
    service = make()
    token = (await service.start("github", RID, OWNER)).token
    assert (await until(service, token, "failed")).message == signins.REASONS["env-token"]


async def test_a_missing_login_command_starts_nothing_and_says_so(
    make: Make, fakes: Fakes, tmp_path: Path
) -> None:
    for binary in (None, tmp_path / "no-such-claude", fakes.creds):  # the last: not a program
        service = make(fakes.settings(tmp_path, claude=binary))
        with pytest.raises(ActionError) as err:
            await service.start("claude", RID, OWNER)
        assert err.value.status == 503 and "was not found on this PC" in str(err.value)
        assert service.view().phase == "idle"


async def test_starts_are_limited_and_the_limit_outlives_a_restart(
    make: Make, fakes: Fakes, tmp_path: Path
) -> None:
    service = make(start_limit=2)
    for n in (1, 2):
        token = (await service.start("claude", rid(n), OWNER)).token
        await service.cancel(token, OWNER)
        await until(service, token, "cancelled")
    for again in (service, make(start_limit=2)):  # the same desk, then a restarted one
        with pytest.raises(ActionError, match="Too many sign-in attempts. Try again in") as err:
            await again.start("claude", rid(9), OWNER)
        assert err.value.status == 429
    assert (tmp_path / "data" / "signin-starts").stat().st_mode & 0o777 == 0o600
    assert "event=refused reason=rate-limited" in service.audit_path.read_text()
    # an old attempt no longer counts
    assert make(start_limit=2, start_window_s=0.01)._claim(time.time() + 1) == 0.0


async def test_only_the_page_that_started_it_sees_the_link_or_acts_on_it(
    make: Make, fakes: Fakes
) -> None:
    service = make()
    token = (await service.start("claude", RID, OWNER)).token
    await until(service, token, "waiting")
    for other in ("", "x", token[:-1], token + "x", "é" * 5, "a" * 5000):
        seen = service.view(other)
        assert seen.phase == "waiting" and not seen.mine  # it learns a sign-in is running
        assert seen.link is None and seen.code is None and not seen.token and not seen.takes_code
        with pytest.raises(ActionError, match="did not start the sign-in"):
            await service.send_code(other, PASTED)
    assert not fakes.read("claude.got")
    assert service.view(token).link == LINK  # and the starting page still has it
    # any owner page may stop it, and still learns nothing
    stopped = await service.cancel("", OWNER)
    assert stopped.link is None and not stopped.mine
    assert (await until(service, "", "cancelled")).link is None


async def test_the_audit_log_names_who_which_and_how_it_went(make: Make, fakes: Fakes) -> None:
    service = make()
    token = (await service.start("claude", RID, "  someone\nevent=forged @example.test  ")).token
    await until(service, token, "waiting")
    await service.send_code(token, PASTED)
    await until(service, token, "ok")
    lines = service.audit_path.read_text().splitlines()
    assert len(lines) == 2 and service.audit_path.stat().st_mode & 0o777 == 0o600
    stamp = r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d[+-]\d{4}"
    who = "who=someoneevent=forged@example.test via=desk provider=claude"
    assert re.fullmatch(f"{stamp} {who} event=started", lines[0])
    assert re.fullmatch(f"{stamp} {who} event=ok", lines[1])


async def test_stopping_the_service_stops_the_login_command(make: Make, fakes: Fakes) -> None:
    service = make()
    token = (await service.start("claude", RID, OWNER)).token
    await until(service, token, "waiting")
    await service.stop()
    assert not fakes.alive("claude") and service.view(token).link is None
    assert "reason=shutdown" in service.audit_path.read_text()


# ---- the page and its actions --------------------------------------------------------------


def _ctx() -> pages.Ctx:
    return pages.Ctx(CSRF.csrf(), nxt="/signins")


def act(c: TestClient, name: str, headers: dict | None = None, **fields: str):
    data = {"csrf": CSRF.csrf(), "next": "/signins", **fields}
    return c.post(f"/act/{name}", content=urlencode(data), headers=headers or JSON)


def begin(c: TestClient, provider: str = "claude") -> str:
    """Both taps of Renew, as the page script sends them. Returns the page's token."""
    first = act(c, "signin", provider=provider).json()
    assert first["ok"] and first["confirm"] and first["extra"] == {"provider": provider}
    second = act(c, "signin", provider=provider, rid=first["rid"], step=first["step"])
    assert second.status_code == 200, second.text
    return second.json()["flow"]


def poll(c: TestClient, token: str, needle: str, tries: int = 300) -> dict:
    for _ in range(tries):
        body = act(c, "signin-flow", flow=token).json()
        if needle in body["html"]:
            return body
        time.sleep(0.02)
    raise AssertionError(f"{needle!r} never showed: {body}")


@pytest.fixture
def app_settings(fakes: Fakes, home: Path, tmp_path: Path) -> GatewaySettings:  # noqa: F811
    return fakes.settings(tmp_path, home)


def test_the_page_lists_each_sign_in_with_renew_where_the_desk_can_do_it(
    app_settings: GatewaySettings,
) -> None:
    with _client(create_app(app_settings, tokens=CSRF)) as c:
        res = c.get("/signins", headers=GOOD)
        assert res.status_code == 200
        main = _main(res.text)
    assert "<h1>Sign-ins</h1>" in main and main.count("<li class='mrow sgrow") == 3
    for label in ("<b>Claude</b>", "<b>GitHub</b>", "<b>Tailscale, this PC</b>"):
        assert label in main
    assert ">21 Nov 2026, 4:51 PM</time>" in main  # the Claude sign-in's end, with its year
    assert main.count("action='/act/signin'") == 2 and main.count(">Renew</button>") == 2
    for key in ("claude", "github"):
        assert f'name=provider value="{key}"' in main
    assert "tailscale" not in re.findall(r'name=provider value="(\w+)"', main)
    assert 'href="https://login.tailscale.com/admin/machines"' in main
    assert "Disable key expiry" in main
    assert "<section id=signin-flow class=sgflow></section>" in main  # no flow: an empty panel
    assert "<form id=signin-poll method=post action='/act/signin-flow' hidden>" in main
    assert res.text.count("<script") == 1 and " data-signins" in res.text
    assert "<a href='/machine' aria-current=page>Machine</a>" in res.text  # filed under Machine
    assert _safe_next("/signins") == "/signins"


def test_the_machine_page_points_at_the_sign_ins_page(app_settings: GatewaySettings) -> None:
    with _client(create_app(app_settings, tokens=CSRF)) as c:
        main = _main(c.get("/machine", headers=GOOD).text)
    assert "<a href='/signins'>Sign-ins page</a>" in main
    row = machine.claude_row(Reading(NOW + 2 * DAY, NOW), NOW)
    assert row.detail == "Renew it on the Sign-ins page"


@pytest.mark.parametrize(
    "bad",
    [
        {"tailscale-user-login": "intruder@example.test"},
        {"tailscale-user-login": ""},
        {"host": "evil.example"},
        {"origin": "https://evil.example"},
    ],
)
def test_only_the_owner_sees_or_starts_anything(
    app_settings: GatewaySettings, fakes: Fakes, bad: dict
) -> None:
    headers = {**JSON, **bad}
    with _client(create_app(app_settings, tokens=CSRF)) as c:
        token = begin(c)
        poll(c, token, "PLANTEDlink")
        assert c.get("/signins", headers={**GOOD, **bad}).status_code == 403
        for name in ("signin", "signin-flow", "signin-code", "signin-cancel"):
            res = act(c, name, headers, provider="github", flow=token, code=PASTED)
            assert res.status_code == 403 and "PLANTED" not in res.text
        assert not fakes.read("claude.got") and not fakes.read("gh.argv")
        assert fakes.alive("claude")  # the stranger's cancel stopped nothing


def test_a_stranger_past_the_tailnet_gets_nothing(app_settings: GatewaySettings) -> None:
    app = create_app(app_settings, tokens=CSRF)
    with TestClient(app, client=("203.0.113.9", 5000)) as c:  # not the tailscale serve proxy
        assert c.get("/signins", headers=GOOD).status_code == 403
        assert act(c, "signin", provider="claude").status_code == 403


def test_every_action_needs_the_csrf_token_and_a_same_site_post(
    app_settings: GatewaySettings, fakes: Fakes
) -> None:
    with _client(create_app(app_settings, tokens=CSRF)) as c:
        token = begin(c)
        for name in ("signin", "signin-flow", "signin-code", "signin-cancel"):
            body = urlencode({"csrf": "wrong", "flow": token, "provider": "github", "code": PASTED})
            assert c.post(f"/act/{name}", content=body, headers=JSON).status_code == 403
            cross = {**JSON, "sec-fetch-site": "cross-site"}
            assert act(c, name, cross, flow=token, provider="github").status_code == 403
            assert c.get(f"/act/{name}", headers=GOOD).status_code in (404, 405)
        assert fakes.alive("claude") and not fakes.read("gh.argv")


def test_renew_runs_nothing_until_the_step_up_is_passed(
    app_settings: GatewaySettings, fakes: Fakes
) -> None:
    with _client(create_app(app_settings, tokens=CSRF)) as c:
        first = act(c, "signin", provider="claude").json()
        assert first["confirm"] and first["label"] == "Tap again to renew"
        assert "Claude's own login command" in first["detail"] and "10 min" in first["detail"]
        rid_, step = first["rid"], first["step"]
        other = CSRF.step_up("signin", f"github:{rid_}")
        wake = CSRF.step_up("wake", f"claude:{rid_}")
        old = CSRF.step_up("signin", f"claude:{rid_}", now=time.time() - 3600)
        for fields in (
            {"rid": rid_},  # no step
            {"rid": rid_, "step": "1.forged"},
            {"rid": rid_, "step": other},  # a step for another sign-in
            {"rid": rid_, "step": wake},  # a step for another action
            {"rid": rid_, "step": old},  # an expired one
            {"rid": rid(7), "step": step},  # this step, another request
        ):
            again = act(c, "signin", provider="claude", **fields).json()
            assert again["ok"] and again["confirm"] and "flow" not in again
        assert not fakes.read("claude.argv")  # nothing ran
        assert act(c, "signin", provider="nope", rid=rid_, step=step).status_code == 404

        started = act(c, "signin", provider="claude", rid=rid_, step=step).json()
        assert started["ok"] and started["active"] and started["flow"]
        assert started["message"] == "The Claude sign-in has started."
        poll(c, started["flow"], "PLANTEDlink")
        # the same confirmed request cannot start a second one, even after this one ends
        act(c, "signin-cancel", flow=started["flow"])
        poll(c, started["flow"], "was cancelled")
        replay = act(c, "signin", provider="claude", rid=rid_, step=step)
        assert replay.status_code == 409 and "already used" in replay.json()["message"]


def test_the_flow_from_the_page_link_then_code_then_the_new_expiry(
    app_settings: GatewaySettings, fakes: Fakes
) -> None:
    with _client(create_app(app_settings, tokens=CSRF)) as c:
        token = begin(c)
        html = poll(c, token, "sgf__link")["html"]
        assert f'<a class=sgf__link href="{LINK.replace("&", "&amp;")}" target=_blank' in html
        assert 'rel="noopener noreferrer">Open the Claude sign-in page ↗</a>' in html
        assert "<span class=sgf__host>claude.com</span>" in html
        assert "action='/act/signin-code'" in html and "name=code" in html
        assert f'name=flow value="{token}"' in html and " data-ends='" in html
        assert "Stops by itself at <time class=reset" in html

        bad = act(c, "signin-code", flow=token, code="two words")
        assert bad.status_code == 400 and "two words" not in bad.text
        sent = act(c, "signin-code", flow=token, code=PASTED).json()
        assert sent["ok"] and "PLANTED" not in json.dumps(sent)  # the code is not echoed back
        done = poll(c, token, "Claude is signed in.")
        assert not done["active"] and "sgf--ok" in done["html"]
        assert "Good until <time class=date" in done["html"] and "PLANTED" not in done["html"]
        # the rows show the new date; Renew is offered again
        main = _main(c.get("/signins", headers=GOOD).text)
        assert ">20 May 2027, 3:51 PM</time>" in main and main.count(">Renew</button>") == 2


def test_another_tab_learns_a_sign_in_is_running_and_nothing_more(
    app_settings: GatewaySettings, fakes: Fakes
) -> None:
    with _client(create_app(app_settings, tokens=CSRF)) as c:
        token = begin(c)
        poll(c, token, "PLANTEDlink")
        page = c.get("/signins", headers=GOOD).text
        main = _main(page)
        assert "PLANTED" not in page and token not in page
        assert "<section id=signin-flow class=sgflow data-active>" in main
        assert "Its link is shown only on the page that started it" in main
        assert "Stop that sign-in</button>" in main and ">Renew</button>" not in main
        assert main.count("A sign-in is running. Renew is back when it ends.") == 2
        for other in ("", "guess", token[:-2]):
            seen = act(c, "signin-flow", flow=other)
            assert seen.json()["active"] and "PLANTED" not in seen.text and token not in seen.text
            code = act(c, "signin-code", flow=other, code=PASTED)
            assert code.status_code == 409 and "PLANTED" not in code.text
        # the token is only read from the request body, never from the address
        url = f"/act/signin-flow?flow={token}"
        by_url = c.post(url, content=urlencode({"csrf": CSRF.csrf()}), headers=JSON)
        assert by_url.json()["active"] and "PLANTED" not in by_url.text
        second = act(c, "signin", provider="github").json()
        blocked = act(c, "signin", provider="github", rid=second["rid"], step=second["step"])
        assert blocked.status_code == 409 and "already running" in blocked.json()["message"]
        assert not fakes.read("claude.got") and not fakes.read("gh.argv")
        stopped = act(c, "signin-cancel").json()  # any owner page may stop it
        assert stopped["ok"] and "PLANTED" not in json.dumps(stopped)
        poll(c, "", "was cancelled")


def test_the_link_and_the_code_never_reach_a_log_a_file_the_stream_or_another_page(
    app_settings: GatewaySettings,
    fakes: Fakes,
    home: Path,  # noqa: F811
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    published: list[str] = []
    monkeypatch.setattr(LiveHub, "publish", lambda self, scope: published.append(scope))
    caplog.set_level(logging.DEBUG)

    def firstmate_files() -> list[str]:
        """Everything in firstmate's home but the test's own fakes and the desk's data."""
        mine_ = (fakes.dir, tmp_path / "data")
        return sorted(
            str(p.relative_to(home))
            for p in home.rglob("*")
            if not any(p == d or d in p.parents for d in mine_)
        )

    before = firstmate_files()
    others: dict[str, str] = {}  # what a page that did not start the flow is given
    mine: dict[str, str] = {}  # what the starting page is given once the code is sent
    with _client(create_app(app_settings, tokens=CSRF)) as c:
        token = begin(c)
        owner = poll(c, token, "PLANTEDlink")["html"]
        assert LINK.replace("&", "&amp;") in owner  # the starting page does get the link
        others["page"] = c.get("/signins", headers=GOOD).text
        others["machine"] = c.get("/machine", headers=GOOD).text
        others["ask"] = act(c, "signin-flow").text
        mine["code sent"] = act(c, "signin-code", flow=token, code=PASTED).text
        mine["ended"] = json.dumps(poll(c, token, "Claude is signed in."))
        mine["asked again"] = act(c, "signin-flow", flow=token).text
        others["page after"] = c.get("/signins", headers=GOOD).text
    assert fakes.read("claude.got") == PASTED + "\n"  # the code did reach the login command
    files = {p.name: p.read_text() for p in (tmp_path / "data").rglob("*") if p.is_file()}
    assert set(files) >= {"signin-audit.log", "signin-starts"}
    for where, text in {**others, **mine, **files, "log": caplog.text}.items():
        assert "PLANTED" not in text, where  # no link, no pasted code
    for where, text in {**others, **files, "log": caplog.text}.items():
        assert token not in text, where  # and the page's token stays with that page
    assert token not in mine["ended"] + mine["asked again"]
    for record in caplog.records:
        assert "PLANTED" not in repr(record.__dict__)
    # nothing about a sign-in goes on the event stream: the only event that may fire here is
    # the Machine page's own "readings changed" tick, which carries no data at all
    assert set(published) <= {"machine"}
    assert firstmate_files() == before  # nothing written to firstmate
    audit = (tmp_path / "data" / "signin-audit.log").read_text().splitlines()
    assert [line.split(" ", 1)[1] for line in audit] == [
        f"who={OWNER} via=desk provider=claude event=started",
        f"who={OWNER} via=desk provider=claude event=ok",
    ]
    journal = [r.getMessage() for r in caplog.records if r.name == "hive.gateway.audit"]
    assert f"gateway-audit action=signin subject=claude outcome=started who={OWNER}" in journal
    assert f"gateway-audit action=signin subject=claude outcome=ok who={OWNER}" in journal


async def test_a_finished_flow_keeps_nothing_in_memory(make: Make, fakes: Fakes) -> None:
    service = make()
    token = (await service.start("claude", RID, OWNER)).token
    await until(service, token, "waiting")
    flow = service._flow
    assert flow is not None and flow.link == LINK
    await service.send_code(token, PASTED)
    assert not flow.raw and flow.tail == ""  # from the paste on, output is not kept
    await until(service, token, "ok")
    assert "PLANTED" not in repr(vars(flow)) + repr(vars(service))


def test_without_the_page_script_each_step_is_a_page(
    app_settings: GatewaySettings, fakes: Fakes
) -> None:
    with _client(create_app(app_settings, tokens=CSRF)) as c:
        confirm = act(c, "signin", GOOD, provider="github")
        assert confirm.status_code == 200 and "<h1>Renew the GitHub sign-in</h1>" in confirm.text
        fields = dict(re.findall(r'<input type=hidden name=(\w+) value="([^"]*)">', confirm.text))
        assert fields["provider"] == "github" and fields["next"] == "/signins"
        assert not fakes.read("gh.argv")
        page = act(c, "signin", GOOD, **fields)
        assert page.status_code == 200 and "<h1>Sign-ins</h1>" in page.text
        token = re.search(r'name=flow value="([^"]+)"', page.text).group(1)
        assert "<form id=signin-poll method=post action='/act/signin-flow'>" in page.text
        assert ">Check again</button>" in page.text
        for _ in range(200):
            page = act(c, "signin-flow", GOOD, flow=token)
            if ONE_TIME in page.text:
                break
            time.sleep(0.02)
        assert f"<strong class=sgf__code>{ONE_TIME}</strong>" in page.text
        assert f'<a class=sgf__link href="{DEVICE}"' in page.text
        assert "name=code" not in _main(page.text)  # GitHub takes no pasted code
        ended = act(c, "signin-cancel", GOOD, flow=token)
        assert ended.status_code == 200 and "<a href='/signins'>Back to Sign-ins</a>" in ended.text
        refused = act(c, "signin-code", GOOD, flow=token, code=PASTED)
        assert refused.status_code == 409 and "No sign-in is running" in refused.text


SLOW = 'echo $$ > "$0.pid"\nsleep 30'


def test_every_action_and_the_page_answer_within_a_second_when_the_commands_hang(
    fakes: Fakes,
    home: Path,  # noqa: F811
    tmp_path: Path,
) -> None:
    fakes.set_claude(SLOW)
    fakes.set_gh(body=SLOW)
    _exe(fakes.tailscale, "sleep 30")
    timed: dict[str, float] = {}

    def clock(label: str, call: Callable[[], object]):
        start = time.monotonic()
        result = call()
        timed[label] = time.monotonic() - start
        return result

    with _client(create_app(fakes.settings(tmp_path, home), tokens=CSRF)) as c:
        for label in ("page, nothing read yet", "page, reads still hanging"):
            page = clock(label, lambda: c.get("/signins", headers=GOOD))
            assert page.status_code == 200 and _main(page.text).count("mrow--unknown") == 3
        first = clock("confirm", lambda: act(c, "signin", provider="claude")).json()
        fields = {"provider": "claude", "rid": first["rid"], "step": first["step"]}
        token = clock("start", lambda: act(c, "signin", **fields)).json()["flow"]
        ask = clock("ask", lambda: act(c, "signin-flow", flow=token)).json()
        assert ask["active"] and "Starting the Claude login command" in ask["html"]
        code = clock("code", lambda: act(c, "signin-code", flow=token, code=PASTED))
        assert code.status_code == 409  # no link yet, so no code is taken
        stopped = clock("cancel", lambda: act(c, "signin-cancel", flow=token)).json()
        assert stopped["ok"]
        poll(c, token, "was cancelled")
        assert not fakes.alive("claude")
        start = time.monotonic()
    timed["shutdown"] = time.monotonic() - start
    assert all(took < 1.0 for took in timed.values()), timed


def test_closing_the_desk_stops_a_running_login_command(
    app_settings: GatewaySettings, fakes: Fakes
) -> None:
    with _client(create_app(app_settings, tokens=CSRF)) as c:
        poll(c, begin(c), "PLANTEDlink")
        assert fakes.alive("claude")
    assert not fakes.alive("claude")


# ---- how each state of the flow reads -------------------------------------------------------


def _view(**changes: object) -> signins.FlowView:
    base: dict = {
        "provider": "claude",
        "label": "Claude",
        "phase": "waiting",
        "mine": True,
        "link": LINK,
        "takes_code": True,
        "started_at": NOW,
        "ends_at": NOW + 600,
        "token": "tok-123",
    }
    return signins.FlowView(**{**base, **changes})


def test_the_panel_reads_plainly_in_every_state() -> None:
    ctx = _ctx()
    assert pages.signin_flow(signins.FlowView(), ctx) == ""
    starting = pages.signin_flow(_view(phase="starting", link=None, takes_code=False), ctx)
    assert "Starting the Claude login command…" in starting and "Cancel</button>" in starting
    checking = pages.signin_flow(_view(phase="checking", link=None, takes_code=False), ctx)
    assert "Checking with Claude…" in checking and "sgf__link" not in checking
    stopping = pages.signin_flow(_view(phase="stopping", link=None, takes_code=False), ctx)
    assert "Stopping…" in stopping and "<button" not in stopping
    retry = pages.signin_flow(
        _view(note="That code was not accepted. Paste the whole code again."), ctx
    )
    assert "<p class=sgf__note role=alert>That code was not accepted." in retry
    other = pages.signin_flow(_view(mine=False, link=None, takes_code=False, token=""), ctx)
    assert "Stop that sign-in</button>" in other and "data-ends" not in other
    assert 'name=flow value=""' in other
    failed = pages.signin_flow(
        _view(phase="failed", message=signins.REASONS["not-understood"], ended_at=NOW), ctx
    )
    assert "class='sgf sgf--failed' role=status" in failed and "<button" not in failed
    ok = pages.signin_flow(
        _view(phase="ok", message="Claude is signed in.", ended_at=NOW, expires=NOW + 180 * DAY),
        ctx,
    )
    assert "Claude is signed in. Good until <time class=date" in ok and "sgf--ok" in ok
    hostile = pages.signin_flow(_view(label="<b>x", note="<script>", code='"><i>'), ctx)
    assert "<script>" not in hostile and "<b>x" not in hostile and '"><i>' not in hostile


# ---- the page in a real browser -----------------------------------------------------------

LAYOUT_DRIVER = """
var f=q('#f');
if(!(f.contentDocument&&f.contentDocument.querySelector('.mach')))
await new Promise(function(r){f.addEventListener('load',r);});
var w=f.contentWindow,d=w.document,e=d.documentElement;
function all(s){return [].slice.call(d.querySelectorAll(s));}
function tall(x){return Math.round(x.getBoundingClientRect().height);}
var a=d.getElementById('alerts');a.hidden=false;a.textContent='Alerts off';
R.iw=w.innerWidth;R.ih=w.innerHeight;R.sw=e.scrollWidth;R.sh=e.scrollHeight;
R.rows=all('.sgrow').length;
R.cut=all('.chrome *,.mach *').filter(function(x){
return x.offsetParent&&x.getBoundingClientRect().right>w.innerWidth+0.5;}).length;
R.navTops=all('.chrome__brand,.chrome nav a,#alerts').map(function(x){
return Math.round(x.getBoundingClientRect().top);});
R.chips=all('.sgrow .mstate').map(function(x){return Math.round(x.getBoundingClientRect().width);});
R.taps=all('.mach button,.mach a.sgf__link,.mach input[type=text]').map(tall);
var l=d.querySelector('.sgf__link');R.link=l?tall(l):0;
R.dark=w.getComputedStyle(d.body).backgroundColor;
"""


def _framed(tmp_path: Path, page: str, size: tuple[int, int], *flags: str) -> dict:
    import html as html_mod

    host = (
        "<!doctype html><html><body style='margin:0'>"
        f"<iframe id=f width={size[0]} height={size[1]} style='border:0;display:block' "
        f'srcdoc="{html_mod.escape(page, quote=True)}"></iframe></body></html>'
    )
    return _in_chrome(tmp_path, host, LAYOUT_DRIVER, (1400, 1400), *flags)


def _panel(flow: signins.FlowView, tmp_path: Path, **states: object) -> signins.Panel:
    rows = signins.build_rows(_states(**states), NOW, _plain(tmp_path))
    return signins.Panel(rows, flow, NOW)


FLOWS = {
    "idle": signins.FlowView(),
    "claude": _view(note="That code was not accepted. Paste the whole code again."),
    "github": _view(
        provider="github", label="GitHub", link=DEVICE, code=ONE_TIME, takes_code=False
    ),
    "other-tab": _view(mine=False, link=None, takes_code=False, token=""),
    "failed": _view(phase="failed", message=signins.REASONS["not-understood"], ended_at=NOW),
}


@needs_chrome
@pytest.mark.parametrize("name", SIZES)
@pytest.mark.parametrize("flow", FLOWS)
def test_the_page_fits_phone_and_ipad_without_sideways_scroll(
    tmp_path: Path, name: str, flow: str
) -> None:
    size = SIZES[name]
    page = pages.render_signins(_panel(FLOWS[flow], tmp_path, claude=NOW + 2 * DAY), _ctx())
    r = _framed(tmp_path, page, size)
    assert (r["iw"], r["ih"]) == size, r
    assert r["rows"] == 3
    assert r["sw"] <= r["iw"] and r["cut"] == 0, r  # no sideways scroll, nothing cut off
    assert len(set(r["navTops"][1:])) == 1, r  # the header links stay on one row
    if size[0] >= 360:
        assert len(set(r["navTops"])) == 1, r
    assert len(set(r["chips"])) == 1, r  # equal chips, so the labels line up down the page
    assert r["taps"] and min(r["taps"]) >= 44, r  # every control is a full touch target
    if flow in ("claude", "github"):
        assert r["link"] >= 56, r  # the sign-in link is the biggest thing to tap
    if name.startswith("ipad") and flow in ("idle", "claude"):
        assert r["sh"] <= r["ih"], r  # the whole page in one screen


COLOUR_DRIVER = """
var cs=function(sel,prop){var x=q(sel);return x?getComputedStyle(x)[prop]:'missing';};
R.page=cs('body','backgroundColor');R.panel=cs('.sgf','backgroundColor');
R.edge=cs('.sgf','borderTopColor');R.link=cs('.sgf__link','backgroundColor');
R.linkText=cs('.sgf__link','color');R.note=cs('.sgf__note','color');
R.box=cs('.sgf__paste input[type=text]','backgroundColor');
R.boxText=cs('.sgf__paste input[type=text]','color');
R.row=cs('.sgrow','backgroundColor');R.why=cs('.sgrow__why','color');
R.host=cs('.sgf__host','color');
"""


@needs_chrome
def test_the_page_follows_light_and_dark(tmp_path: Path) -> None:
    """Every colour on the page comes from the desk's tokens, so each one turns with the theme."""
    page = pages.render_signins(_panel(FLOWS["claude"], tmp_path), _ctx())
    dark = page.replace("@media (prefers-color-scheme:dark){:root{", "@media all{:root{", 1)
    assert dark != page
    (tmp_path / "light").mkdir()
    (tmp_path / "dark").mkdir()
    lit = _in_chrome(tmp_path / "light", page, COLOUR_DRIVER, (820, 1180))
    dim = _in_chrome(tmp_path / "dark", dark, COLOUR_DRIVER, (820, 1180))
    assert (lit["page"], dim["page"]) == ("rgb(242, 236, 216)", "rgb(22, 20, 15)")
    assert "missing" not in lit.values()
    same = [k for k in lit if lit[k] == dim[k]]
    assert same == [], same
    # the big link stays readable in both: light on dark, then dark on light
    assert (lit["link"], lit["linkText"]) == ("rgb(31, 24, 18)", "rgb(250, 247, 237)")
    assert (dim["link"], dim["linkText"]) == ("rgb(239, 233, 218)", "rgb(32, 29, 22)")


FLOW_FETCH = (
    FAKE_LIVE
    + """
window.__calls=[];window.__answers={};
window.fetch=function(url,opt){var u=String(url),b=opt&&opt.body?String(opt.body):'';
var name=u.split('/act/')[1];
if(!name)return Promise.resolve({ok:true,
text:function(){return Promise.resolve(window.__next||'');}});
window.__calls.push(name+' '+b);var a=window.__answers[name],j=typeof a==='function'?a(b):a;
return Promise.resolve({ok:true,status:200,json:function(){return Promise.resolve(j);}});};
"""
)
FLOW_DRIVER = """
function nap(ms){return new Promise(function(r){setTimeout(r,ms);});}
function calls(n){return window.__calls.filter(function(c){return c.indexOf(n+' ')===0;});}
var OWN=window.__own,DONE=window.__done;
var fm=q('.sgrow__act form'),b=fm.querySelector('button');
window.__answers.signin=function(body){return body.indexOf('step=')<0?
{ok:true,confirm:true,step:'STEP',rid:'web-00000000000000a1',extra:{provider:'claude'},
label:'Tap again to renew',detail:'This runs the login command.'}:
{ok:true,message:'started',active:true,flow:'TOKEN123',html:OWN};};
window.__answers['signin-flow']={ok:true,message:'',active:true,html:OWN};
fm.requestSubmit(b);await nap(50);
R.armed=b.classList.contains('is-armed')&&b.textContent==='Tap again to renew';
R.warned=fm.querySelector('.act-warn').textContent;
R.nothingYet=!q('#signin-flow .sgf__link');
fm.requestSubmit(b);await nap(50);
R.sentStep=calls('signin')[1].indexOf('step=STEP')>=0&&calls('signin')[1].indexOf('provider=claude')>=0;
R.link=q('#signin-flow .sgf__link').getAttribute('href');
R.kept=sessionStorage.getItem('hive-signin');
R.label=b.textContent;
// a refresh of the page (which never holds the link) must not take the panel away
window.__next=document.documentElement.outerHTML.replace('Valid, 2 days left','Valid, 1 day left');
window.__es.onopen();window.__es.l.desk();await nap(100);
R.refreshed=q('main').textContent.indexOf('Valid, 1 day left')>=0;
R.linkAfterRefresh=!!q('#signin-flow .sgf__link');
// the page asks how it is going, carrying its token in the body
await nap(2100);
R.asked=calls('signin-flow').length>=1&&calls('signin-flow')[0].indexOf('flow=TOKEN123')>=0;
// while a code is being typed the panel is left alone
var box=q('#signin-flow input[name=code]');box.value='typed';
window.__answers['signin-flow']={ok:true,message:'',active:true,
html:OWN.replace('Paste it here','PASTE IT HERE')};
await nap(2100);
R.typedKept=q('#signin-flow input[name=code]').value==='typed';
// sending the code clears the box and shows the answer
window.__answers['signin-code']={ok:true,message:'sent',active:true,html:window.__checking};
q('#signin-flow form.sgf__paste').requestSubmit();await nap(50);
var sent=calls('signin-code')[0];
R.codeSent=sent.indexOf('code=typed')>=0&&sent.indexOf('flow=tok-123')>=0;
R.checking=q('#signin-flow').textContent.indexOf('Checking with Claude')>=0
&&!q('#signin-flow input[name=code]');
// it ends: the result shows and the token is dropped
window.__answers['signin-flow']={ok:true,message:'',active:false,html:DONE};
await nap(2100);
R.done=q('#signin-flow').textContent.indexOf('Claude is signed in.')>=0;
R.forgot=sessionStorage.getItem('hive-signin');
var n=calls('signin-flow').length;await nap(2500);
R.stoppedAsking=calls('signin-flow').length===n;
"""
EXPIRE_DRIVER = """
function nap(ms){return new Promise(function(r){setTimeout(r,ms);});}
sessionStorage.setItem('hive-signin','TOKEN123');
window.__answers['signin-flow']={ok:true,message:'',active:true,
html:window.__own.replace(/data-ends='[0-9]+'/,"data-ends='"+(Date.now()+400)+"'")};
window.__es.onopen();window.__es.l.desk();
// the page was loaded with a token kept from before a reload: it asks at once
"""


def _script_page(tmp_path: Path, flow: signins.FlowView) -> str:
    page = pages.render_signins(_panel(flow, tmp_path, claude=NOW + 2 * DAY), _ctx())
    ends = time.time() + 600  # the page blanks a panel whose end has passed: use the real clock
    fragments = {
        "__own": pages.signin_flow(_view(ends_at=ends), _ctx()),
        "__checking": pages.signin_flow(
            _view(phase="checking", link=None, takes_code=False, ends_at=ends), _ctx()
        ),
        "__done": pages.signin_flow(
            _view(phase="ok", message="Claude is signed in.", ended_at=NOW, token=""), _ctx()
        ),
    }
    seed = "".join(f"window.{k}={json.dumps(v)};" for k, v in fragments.items())
    return page.replace("<script>", f"<script>{seed}</script><script>", 1)


@needs_chrome
def test_the_page_script_arms_renew_shows_the_flow_and_keeps_it_through_refreshes(
    tmp_path: Path,
) -> None:
    page = _script_page(tmp_path, signins.FlowView())
    r = _in_chrome(
        tmp_path, page, FLOW_DRIVER, (820, 1180), "--virtual-time-budget=20000", prelude=FLOW_FETCH
    )
    assert r["armed"] and r["warned"] == "This runs the login command." and r["nothingYet"]
    assert r["sentStep"] and r["link"] == LINK and r["kept"] == "TOKEN123"
    assert r["label"] == "Renew"
    assert r["refreshed"] and r["linkAfterRefresh"]
    assert r["asked"] and r["typedKept"]
    assert r["codeSent"] and r["checking"]
    assert r["done"] and r["forgot"] is None and r["stoppedAsking"]


@needs_chrome
def test_a_reloaded_page_picks_its_flow_up_again_and_the_link_leaves_at_the_time_out(
    tmp_path: Path,
) -> None:
    driver = (
        "function nap(ms){return new Promise(function(r){setTimeout(r,ms);});}"
        "await nap(100);"
        "R.asked=window.__calls[0];R.link=!!q('#signin-flow .sgf__link');"
        "window.__answers['signin-flow']={ok:false,message:'offline'};"
        "await nap(2500);"
        "R.gone=!q('#signin-flow .sgf__link');R.says=q('#signin-flow').textContent;"
    )
    prelude = (
        FLOW_FETCH
        + "try{sessionStorage.setItem('hive-signin','TOKEN123');}catch(e){}"
        + "window.__answers['signin-flow']=function(){return {ok:true,message:'',active:true,"
        + "html:window.__own.replace(/data-ends='[0-9]+'/,"
        + '"data-ends=\'"+(Date.now()+300)+"\'")};};'
    )
    page = _script_page(tmp_path, _view(mine=False, link=None, takes_code=False, token=""))
    r = _in_chrome(
        tmp_path, page, driver, (820, 1180), "--virtual-time-budget=9000", prelude=prelude
    )
    assert r["asked"].startswith("signin-flow ") and "flow=TOKEN123" in r["asked"]
    assert r["link"]  # the reloaded page got its panel back
    assert r["gone"] and r["says"] == "This sign-in has ended."
