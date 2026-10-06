"""Write actions against a throwaway FM_HOME of fake scripts; the real home is never used."""

from __future__ import annotations

import json
import re
import stat
from pathlib import Path
from urllib.parse import urlencode

import pytest
from fastapi.testclient import TestClient

from hive.gateway.actions import Tokens
from hive.gateway.app import create_app
from hive.gateway.settings import GatewaySettings

FIXTURE = Path(__file__).parent.parent / "fixtures" / "gateway" / "fleet-snapshot.v1.json"
OWNER = "owner@example.test"
HOST = "desk.example.ts.net"
GOOD = {
    "tailscale-user-login": OWNER,
    "host": HOST,
    "origin": f"https://{HOST}",
    "content-type": "application/x-www-form-urlencoded",
}
CSRF = Tokens(b"k" * 32)
RID = "web-0123456789abcdef"

# Every fake script appends one JSON line (argv + stdin) to $FM_HOME/calls.jsonl.
LOGGER = r"""
rec() {
  python3 - "$@" <<'PY'
import json, os, sys
argv = sys.argv[1:]
files = {}
for i, a in enumerate(argv):
    if a == "--decision-file":
        files["decision"] = open(argv[i + 1]).read()
stdin = "" if os.isatty(0) else os.environ.get("FAKE_STDIN", "")
with open(os.path.join(os.environ["FM_HOME"], "calls.jsonl"), "a") as fh:
    fh.write(json.dumps({"script": os.environ["FAKE_NAME"], "argv": argv,
                         "files": files, "stdin": stdin}) + "\n")
PY
}
"""


def _script(home: Path, name: str, body: str) -> None:
    path = home / "bin" / name
    path.write_text(f"#!/usr/bin/env bash\nset -u\nexport FAKE_NAME={name}\n{LOGGER}\n{body}\n")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


@pytest.fixture
def home(tmp_path: Path) -> Path:
    (tmp_path / "bin").mkdir()
    _script(tmp_path, "fm-fleet-snapshot.sh", f"cat '{FIXTURE}'")
    _script(
        tmp_path,
        "fm-captain-hold.sh",
        'IN="$(cat; printf x)"; FAKE_STDIN="${IN%x}" rec "$@"; echo "closed: ${IN%%$\'\\t\'*}"',
    )
    _script(
        tmp_path,
        "fm-inbox.sh",
        'case "$1" in\n'
        '  note) IN="$(cat; printf x)"; FAKE_STDIN="${IN%x}" rec "$@"; '
        'echo \'{"schema":"fm-inbox-note.v1","outcome":"created","id":"n1"}\' ;;\n'
        '  receipts) cat "$FM_HOME/receipts.json" 2>/dev/null || echo "{}" ;;\n'
        "  ready) echo '{\"can_receive\": true}' ;;\n"
        "esac",
    )
    _script(tmp_path, "fm-control.sh", 'rec "$@"; echo "ok: $2"')
    return tmp_path


def _calls(home: Path) -> list[dict]:
    path = home / "calls.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines()]


@pytest.fixture
def client(home: Path) -> TestClient:
    settings = GatewaySettings(
        owner_login=OWNER, allowed_hosts=(HOST,), fm_home=home, snapshot_ttl_s=0
    )
    return TestClient(
        create_app(settings, tokens=CSRF), client=("127.0.0.1", 5000), follow_redirects=False
    )


def post(client: TestClient, name: str, **fields: str):
    data = {"csrf": CSRF.csrf(), "next": "/", "rid": RID, **fields}
    return client.post(f"/act/{name}", content=urlencode(data), headers=GOOD)


def _hidden(html: str, name: str) -> str:
    return re.search(rf'name={name} value="([^"]+)"', html).group(1)


# ---- guards -----------------------------------------------------------------------


def test_csrf_required(client: TestClient, home: Path) -> None:
    res = client.post("/act/chat", content=urlencode({"text": "hi"}), headers=GOOD)
    assert res.status_code == 403
    res = post(client, "chat", text="hi", csrf="wrong")
    assert res.status_code == 403
    assert _calls(home) == []


@pytest.mark.parametrize(
    "extra",
    [{"origin": "https://evil.example"}, {"sec-fetch-site": "cross-site"}],
)
def test_cross_origin_post_refused(client: TestClient, home: Path, extra: dict) -> None:
    data = urlencode({"csrf": CSRF.csrf(), "text": "hi", "rid": "web-0123456789abcdef"})
    res = client.post("/act/chat", content=data, headers={**GOOD, **extra})
    assert res.status_code == 403
    no_origin = {k: v for k, v in GOOD.items() if k != "origin"}
    assert client.post("/act/chat", content=data, headers=no_origin).status_code == 403
    assert _calls(home) == []


def test_post_outside_act_is_405(client: TestClient) -> None:
    assert client.post("/chat", headers=GOOD).status_code == 405


def test_forms_carry_csrf_and_fallback_hides_them(client: TestClient, tmp_path: Path) -> None:
    html = client.get("/", headers={k: v for k, v in GOOD.items() if k != "origin"}).text
    assert CSRF.csrf() in html and "/act/answer" in html and "/act/merge" in html


def test_writes_refused_when_snapshot_unusable(tmp_path: Path) -> None:
    (tmp_path / "bin").mkdir()
    _script(tmp_path, "fm-fleet-snapshot.sh", "echo not-json")
    _script(tmp_path, "fm-inbox.sh", 'rec "$@"')
    settings = GatewaySettings(owner_login=OWNER, allowed_hosts=(HOST,), fm_home=tmp_path)
    c = TestClient(create_app(settings, tokens=CSRF), client=("127.0.0.1", 5000))
    res = post(c, "chat", text="hi")
    assert res.status_code == 503
    assert _calls(tmp_path) == []


# ---- decisions --------------------------------------------------------------------


def test_answer_hold_feeds_the_owner_aware_keyed_intake(client: TestClient, home: Path) -> None:
    res = post(client, "answer", task="beta-hold", text="Use the warm\npalette", release="1")
    assert res.status_code == 200 and "closed: beta-hold" in res.text
    (call,) = _calls(home)
    assert call["script"] == "fm-captain-hold.sh"
    assert call["argv"][:3] == ["answers", "--any-origin", "--source"]
    assert OWNER in call["argv"][3] and "answer" not in call["argv"][:1]
    assert call["stdin"] == "beta-hold\tUse the warm palette\tHive desk\trelease\n"


def test_answer_without_release_and_shell_chars_stay_data(client: TestClient, home: Path) -> None:
    res = post(client, "answer", task="beta-hold", text="$(touch /tmp/pwned); `x`")
    assert res.status_code == 200
    (call,) = _calls(home)
    assert call["stdin"].endswith("\tHive desk\tdone\n")
    assert "$(touch /tmp/pwned)" in call["stdin"]


def test_answer_longer_than_the_intake_keeps_is_refused(client: TestClient, home: Path) -> None:
    res = post(client, "answer", task="beta-hold", text="x" * 513)
    assert res.status_code == 400 and _calls(home) == []


def test_answer_over_the_intake_byte_limit_is_refused(client: TestClient, home: Path) -> None:
    res = post(client, "answer", task="beta-hold", text="é" * 300)
    assert res.status_code == 400 and _calls(home) == []


def test_repeated_answer_runs_once_and_shows_first_result(client: TestClient, home: Path) -> None:
    first = post(client, "answer", task="beta-hold", text="Use the warm palette")
    again = post(client, "answer", task="beta-hold", text="Use the warm palette")
    assert first.status_code == again.status_code == 200
    assert "closed: beta-hold" in again.text
    assert len(_calls(home)) == 1


def test_invalid_answer_can_be_corrected_with_the_same_request_id(
    client: TestClient, home: Path
) -> None:
    assert post(client, "answer", task="beta-hold", text="x" * 7000).status_code == 400
    assert post(client, "answer", task="beta-hold", text="shorter").status_code == 200
    assert len(_calls(home)) == 1


def test_failed_answer_script_is_not_rerun(client: TestClient, home: Path) -> None:
    _script(home, "fm-captain-hold.sh", 'rec "$@"; echo "partial"; exit 1')
    first = post(client, "answer", task="beta-hold", text="x")
    again = post(client, "answer", task="beta-hold", text="x")
    assert first.status_code == again.status_code == 409
    assert len(_calls(home)) == 1


def test_answer_rejects_bad_request_id(client: TestClient, home: Path) -> None:
    assert post(client, "answer", task="beta-hold", text="x", rid="").status_code == 400
    assert _calls(home) == []


@pytest.mark.parametrize("task", ["alpha-build", "no-such-task", "bad id;rm", ""])
def test_answer_only_open_holds(client: TestClient, home: Path, task: str) -> None:
    res = post(client, "answer", task=task, text="x")
    assert res.status_code in (400, 409)
    assert _calls(home) == []


def test_answer_empty_or_oversize_refused(client: TestClient, home: Path) -> None:
    assert post(client, "answer", task="beta-hold", text="  ").status_code == 400
    assert post(client, "answer", task="beta-hold", text="x" * 7000).status_code == 400
    assert _calls(home) == []


def test_answer_script_refusal_is_shown(home: Path) -> None:
    _script(home, "fm-captain-hold.sh", 'echo "skipped: already closed"; exit 1')
    settings = GatewaySettings(
        owner_login=OWNER, allowed_hosts=(HOST,), fm_home=home, snapshot_ttl_s=0
    )
    c = TestClient(create_app(settings, tokens=CSRF), client=("127.0.0.1", 5000))
    res = post(c, "answer", task="beta-hold", text="x")
    assert res.status_code == 409 and "already closed" in res.text


def test_decision_answer_becomes_structured_note(client: TestClient, home: Path) -> None:
    res = post(client, "decision", task="beta-pr", key="board-review", text="Option A")
    assert res.status_code == 200 and "Sent to the first mate." in res.text
    (call,) = _calls(home)
    assert call["script"] == "fm-inbox.sh"
    assert call["argv"] == ["note", "--request-id", "web-0123456789abcdef", "--json", "-"]
    assert call["stdin"].startswith("HIVE-WEB DECISION ANSWER v1\ntask: beta-pr\n")
    assert "decision: board-review" in call["stdin"] and call["stdin"].endswith("Option A\n")


def test_decision_must_be_open(client: TestClient, home: Path) -> None:
    assert post(client, "decision", task="beta-pr", key="other", text="x").status_code == 409
    assert _calls(home) == []


# ---- chat and receipts ------------------------------------------------------------


def test_chat_sends_note_with_request_id(client: TestClient, home: Path) -> None:
    res = post(client, "chat", text="status please")
    assert res.status_code == 200 and "Sent to the first mate." in res.text
    (call,) = _calls(home)
    assert call["argv"] == ["note", "--request-id", "web-0123456789abcdef", "--json", "-"]
    assert call["stdin"] == "status please"


@pytest.mark.parametrize(
    ("reply", "shown"),
    [
        ('echo \'{"outcome":"created"}\'; exit 3', "has not been woken yet"),
        ('echo \'{"outcome":"replay"}\'', "Already sent"),
    ],
)
def test_chat_shows_the_inbox_outcome(
    client: TestClient, home: Path, reply: str, shown: str
) -> None:
    _script(home, "fm-inbox.sh", f'case "$1" in\n  note) cat >/dev/null; {reply} ;;\nesac')
    res = post(client, "chat", text="status please")
    assert res.status_code == 200 and shown in res.text


def test_chat_rejects_bad_request_id_and_empty(client: TestClient, home: Path) -> None:
    assert post(client, "chat", text="x", rid="not-valid").status_code == 400
    assert post(client, "chat", text="  ").status_code == 400
    assert _calls(home) == []


def test_chat_page_shows_receipts_and_replies(client: TestClient, home: Path) -> None:
    receipts = {
        "schema": "fm-inbox-receipts.v1",
        "pending": [
            {
                "id": "n2",
                "at": "2030-01-02T00:00:02Z",
                "body": "later <b>note</b>",
                "acknowledged": False,
                "reply": None,
            },
            {
                "id": "n3",
                "at": "2030-01-02T00:00:03Z",
                "body": "HIVE-WEB TICKET REQUEST v1\naction: edit\n---\nnew title",
                "acknowledged": False,
                "reply": None,
            },
        ],
        "handled": [
            {
                "id": "n1",
                "at": "2030-01-02T00:00:01Z",
                "body": "first",
                "acknowledged": True,
                "reply": {"id": "n1", "at": "2030-01-02T00:00:05Z", "body": "done, applied"},
            },
        ],
        "omitted": [],
    }
    (home / "receipts.json").write_text(json.dumps(receipts))
    html = client.get("/chat", headers={k: v for k, v in GOOD.items() if k != "origin"}).text
    assert "done, applied" in html and "Answered" in html
    assert "waiting for the first mate" in html and "ticket request" in html
    assert "<b>note</b>" not in html and "&lt;b&gt;note&lt;/b&gt;" in html
    assert re.search(r"/act/chat", html)
    # a conversation: oldest first, the reply right after its message, newest last
    assert (
        html.index("first<")
        < html.index("done, applied")
        < html.index("later")
        < html.index("new title")
    )
    assert "data-poll" in html
    assert "2030-01-02T00:00:05+00:00" in html  # <time> the page script localises
    assert "Wed 2 Jan, 11:00 AM" in html


# ---- tickets ----------------------------------------------------------------------


def test_ticket_edit_request_shape(client: TestClient, home: Path) -> None:
    res = post(
        client,
        "ticket",
        mode="edit",
        project="alpha",
        ticket="alpha-docs",
        field="title",
        text="Write the alpha docs, v2",
    )
    assert res.status_code == 200 and "Sent to the first mate." in res.text
    (call,) = _calls(home)
    assert call["stdin"] == (
        "HIVE-WEB TICKET REQUEST v1\naction: edit\nticket: alpha-docs\nproject: alpha\n"
        f"field: title\nfrom: hive web ({OWNER})\n---\nWrite the alpha docs, v2\n"
    )


def test_ticket_create_request_shape(client: TestClient, home: Path) -> None:
    res = post(
        client,
        "ticket",
        mode="create",
        project="alpha",
        title="Add dark mode",
        text="Respect prefers-color-scheme",
    )
    assert res.status_code == 200
    (call,) = _calls(home)
    assert "action: create\nticket: (new)\nproject: alpha\nfield: new\n" in call["stdin"]
    assert call["stdin"].endswith("---\nAdd dark mode\n\nRespect prefers-color-scheme\n")


@pytest.mark.parametrize(
    "fields",
    [
        {"mode": "edit", "project": "alpha", "ticket": "alpha-old", "field": "title", "text": "x"},
        {"mode": "edit", "project": "alpha", "ticket": "ghost", "field": "title", "text": "x"},
        {"mode": "edit", "project": "alpha", "ticket": "alpha-docs", "field": "state", "text": "x"},
        {"mode": "edit", "project": "nope", "ticket": "alpha-docs", "field": "title", "text": "x"},
        {"mode": "create", "project": "alpha", "title": "two\nlines", "text": ""},
        {"mode": "bogus", "project": "alpha"},
    ],
)
def test_ticket_refusals(client: TestClient, home: Path, fields: dict) -> None:
    assert post(client, "ticket", **fields).status_code in (400, 404, 409)
    assert _calls(home) == []


# ---- worker controls --------------------------------------------------------------


def test_control_needs_confirm_step(client: TestClient, home: Path) -> None:
    res = post(client, "control", task="alpha-build", verb="interrupt")
    assert res.status_code == 200 and "Confirm" in res.text
    assert _calls(home) == []
    step = _hidden(res.text, "step")
    rid = _hidden(res.text, "rid")
    done = post(
        client, "control", task="alpha-build", verb="interrupt", step=step, rid=rid, confirm="1"
    )
    assert done.status_code == 200 and "ok: interrupt" in done.text
    (call,) = _calls(home)
    assert call["script"] == "fm-control.sh" and call["argv"] == ["alpha-build", "interrupt"]


def test_repeated_control_confirm_runs_once(client: TestClient, home: Path) -> None:
    res = post(client, "control", task="alpha-build", verb="relaunch")
    fields = {k: _hidden(res.text, k) for k in ("step", "rid", "note")}
    first = post(client, "control", task="alpha-build", verb="relaunch", confirm="1", **fields)
    again = post(client, "control", task="alpha-build", verb="relaunch", confirm="1", **fields)
    assert first.status_code == again.status_code == 200
    assert "ok: relaunch" in first.text and "ok: relaunch" in again.text
    assert len(_calls(home)) == 1


def test_failed_control_is_not_rerun_by_a_repeat_post(client: TestClient, home: Path) -> None:
    _script(home, "fm-control.sh", 'rec "$@"; echo "half relaunched"; exit 1')
    res = post(client, "control", task="alpha-build", verb="relaunch")
    fields = {k: _hidden(res.text, k) for k in ("step", "rid", "note")}
    first = post(client, "control", task="alpha-build", verb="relaunch", confirm="1", **fields)
    again = post(client, "control", task="alpha-build", verb="relaunch", confirm="1", **fields)
    assert first.status_code == again.status_code == 409
    assert "half relaunched" in again.text
    assert len(_calls(home)) == 1


def test_control_step_is_bound_to_request_id(client: TestClient, home: Path) -> None:
    res = post(client, "control", task="alpha-build", verb="interrupt")
    step = _hidden(res.text, "step")
    other = post(client, "control", task="alpha-build", verb="interrupt", step=step)
    assert other.status_code == 200 and "Confirm" in other.text and _calls(home) == []


def test_control_relaunch_passes_note(client: TestClient, home: Path) -> None:
    res = post(client, "control", task="alpha-build", verb="relaunch", note="pick up from tests")
    fields = {k: _hidden(res.text, k) for k in ("step", "rid", "note")}
    post(client, "control", task="alpha-build", verb="relaunch", **fields)
    (call,) = _calls(home)
    assert call["argv"] == ["alpha-build", "relaunch", "--note", "pick up from tests"]


def test_control_step_is_bound_to_task_and_verb(client: TestClient, home: Path) -> None:
    step = CSRF.step_up("control", f"alpha-build:interrupt:{RID}")
    assert (
        post(
            client, "control", task="alpha-build", verb="relaunch", step=step, note="n"
        ).status_code
        == 200
    )  # back to the confirm page
    assert _calls(home) == []
    expired = CSRF.step_up("control", f"alpha-build:interrupt:{RID}", now=1.0)
    res = post(client, "control", task="alpha-build", verb="interrupt", step=expired)
    assert res.status_code == 200 and _calls(home) == []


@pytest.mark.parametrize("verb", ["exit", "teardown", "relaunch;ls", ""])
def test_no_exit_or_teardown(client: TestClient, home: Path, verb: str) -> None:
    step = CSRF.step_up("control", f"alpha-build:{verb}:{RID}")
    res = post(client, "control", task="alpha-build", verb=verb, step=step)
    assert res.status_code == 400 and _calls(home) == []


def test_control_unlisted_worker_refused(client: TestClient, home: Path) -> None:
    res = post(client, "control", task="ghost", verb="interrupt")
    assert res.status_code == 409 and _calls(home) == []


# ---- merge ------------------------------------------------------------------------


def test_merge_word_is_a_note_after_step_up(client: TestClient, home: Path) -> None:
    res = post(client, "merge", task="alpha-build")
    assert res.status_code == 200 and "never merges" in res.text and _calls(home) == []
    done = post(client, "merge", task="alpha-build", step=_hidden(res.text, "step"))
    assert done.status_code == 200 and "Sent to the first mate." in done.text
    (call,) = _calls(home)
    assert call["script"] == "fm-inbox.sh" and call["argv"][0] == "note"
    assert call["stdin"].startswith("HIVE-WEB MERGE WORD v1\ntask: alpha-build\n")
    assert "pr: https://example.invalid/alpha/pull/2" in call["stdin"]


def test_merge_requires_a_listed_merge_approval(client: TestClient, home: Path) -> None:
    step = CSRF.step_up("merge", "beta-hold")
    res = post(client, "merge", task="beta-hold", step=step)
    assert res.status_code == 409 and _calls(home) == []
    assert not any(c["script"] == "fm-captain-hold.sh" for c in _calls(home))


def test_audit_line_per_action(client: TestClient, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level("INFO", logger="hive.gateway.audit")
    post(client, "answer", task="beta-hold", text="secret words")
    lines = [r.getMessage() for r in caplog.records if r.name == "hive.gateway.audit"]
    assert len(lines) == 1 and "action=answer" in lines[0] and "subject=beta-hold" in lines[0]
    assert "secret words" not in lines[0]


def test_unknown_action_404(client: TestClient) -> None:
    assert post(client, "teardown", task="alpha-build").status_code == 404


# ---- delegate bar -----------------------------------------------------------------


def test_delegate_without_focus_is_a_plain_note_to_the_first_mate(
    client: TestClient, home: Path
) -> None:
    res = post(client, "delegate", text="Ship the alpha docs", project="")
    assert res.status_code == 200 and "Sent to the first mate." in res.text
    (call,) = _calls(home)
    assert call["argv"] == ["note", "--request-id", RID, "--json", "-"]
    assert call["stdin"] == "Ship the alpha docs"


def test_delegate_to_a_project_names_it_and_who_answers(client: TestClient, home: Path) -> None:
    res = post(
        client, "delegate", text="Ship the alpha docs", project="alpha", next="/?focus=alpha"
    )
    assert res.status_code == 200 and "href='/?focus=alpha'" in res.text
    (call,) = _calls(home)
    assert call["stdin"] == (
        "HIVE-WEB DELEGATE v1\nproject: alpha\nto: first mate\n"
        f"from: hive web ({OWNER})\n---\nShip the alpha docs\n"
    )


def test_delegate_to_a_second_mates_project(home: Path) -> None:
    data = json.loads(FIXTURE.read_text())
    data["tasks"].append({"id": "beta-mate", "kind": "secondmate", "secondmate_projects": ["beta"]})
    snap = home / "mate.json"
    snap.write_text(json.dumps(data))
    _script(home, "fm-fleet-snapshot.sh", f"cat '{snap}'")
    settings = GatewaySettings(owner_login=OWNER, allowed_hosts=(HOST,), fm_home=home)
    c = TestClient(create_app(settings, tokens=CSRF), client=("127.0.0.1", 5000))
    assert post(c, "delegate", text="Polish beta", project="beta").status_code == 200
    (call,) = _calls(home)
    assert "project: beta\nto: second mate\n" in call["stdin"]


def test_delegate_refusals(client: TestClient, home: Path) -> None:
    assert post(client, "delegate", text="x", project="nope").status_code == 404
    assert post(client, "delegate", text="  ", project="alpha").status_code == 400
    assert _calls(home) == []
