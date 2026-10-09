# ruff: noqa: F811
"""Firstmate's merge posture and worker settings: reader, desk view and fail-safe gates.

Every source is tried present, missing and malformed. A synthetic home is used; the real
firstmate home is never read and nothing here can merge.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from hive.gateway import fmconfig
from tests.gateway.fmcfg import PROJECTS, write_config
from tests.gateway.test_actions import (  # noqa: F401  (fixtures and helpers)
    CSRF,
    GOOD,
    OWNER,
    _calls,
    _hidden,
    client,
    home,
    post,
)

# ---- reader -----------------------------------------------------------------------


def test_projects_parse_mode_and_yolo(tmp_path: Path) -> None:
    p = fmconfig.read_projects(write_config(tmp_path))
    assert p.error == "" and p.ambiguous == {}
    assert p.postures["alpha"] == fmconfig.Posture("no-mistakes", True)
    assert p.postures["beta"] == fmconfig.Posture("direct-PR", False)
    assert p.postures["gamma"] == fmconfig.Posture("local-only", True)
    assert p.postures["legacy"] == fmconfig.Posture("no-mistakes", False)  # firstmate's legacy row


def test_projects_missing_and_empty(tmp_path: Path) -> None:
    assert "not found" in fmconfig.read_projects(write_config(tmp_path, projects=None)).error
    assert fmconfig.read_projects(write_config(tmp_path, projects="# nothing\n")).error


@pytest.mark.parametrize(
    "text,name",
    [
        ("- a [bogus] - x\n", "a"),
        ("- a [no-mistakes direct-PR] - x\n", "a"),
        ("- a [no-mistakes +yolo] - x\n- a [direct-PR] - y\n", "a"),
        ("- [no-mistakes] - nameless\n", "[no-mistakes]"),
    ],
)
def test_projects_malformed_rows_are_ambiguous_not_defaulted(
    tmp_path: Path, text: str, name: str
) -> None:
    p = fmconfig.read_projects(write_config(tmp_path, projects=text))
    assert name in p.ambiguous and name not in p.postures


def test_projects_binary_garbage_is_an_error_not_a_crash(tmp_path: Path) -> None:
    write_config(tmp_path)
    (tmp_path / "data" / "projects.md").write_bytes(b"\xff\xfe\x00\x80")
    assert fmconfig.read_projects(tmp_path).error


def test_dispatch_profiles(tmp_path: Path) -> None:
    d = fmconfig.read_dispatch(write_config(tmp_path))
    assert d.error == ""
    assert d.rules[0].profiles[1] == fmconfig.Profile("codex", "gpt-x", "high")
    assert d.rules[1].profiles == (fmconfig.Profile("claude", "claude-sonnet-x", ""),)
    assert d.default == (fmconfig.Profile("codex", "gpt-y", "medium"),)


@pytest.mark.parametrize(
    "dispatch",
    [
        "{not json",
        "[]",
        '{"rules": "x"}',
        '{"rules": [{"when": "x"}]}',
        '{"rules": [{"when": "x", "use": {"model": "m"}}]}',
        '{"rules": [{"when": "x", "use": []}]}',
        '{"rules": [{"when": "x", "use": {"harness": "c", "effort": 3}}]}',
        "{}",
    ],
)
def test_dispatch_malformed_is_an_error(tmp_path: Path, dispatch: str) -> None:
    d = fmconfig.read_dispatch(write_config(tmp_path, dispatch=dispatch))
    assert d.error and not d.absent and not d.rules and not d.default


def test_dispatch_missing_is_absent(tmp_path: Path) -> None:
    d = fmconfig.read_dispatch(write_config(tmp_path, dispatch=None))
    assert d.absent and "not found" in d.error


def test_harness_and_permission_sources(tmp_path: Path) -> None:
    write_config(tmp_path, harness="codex\n", permission=None)
    assert fmconfig.read_harness(tmp_path).text == "codex"
    perm = fmconfig.read_permission(tmp_path)
    assert perm.text == "bypass" and perm.absent and not perm.error  # firstmate's own default
    for text in ("auto\n", "bypass"):
        write_config(tmp_path, permission=text)
        assert fmconfig.read_permission(tmp_path).text == text.strip()
    for bad in ("yolo", "", "auto bypass"):
        write_config(tmp_path, permission=bad)
        assert fmconfig.read_permission(tmp_path).error
    for bad in ("", "a b"):
        write_config(tmp_path, harness=bad)
        assert fmconfig.read_harness(tmp_path).error
    write_config(tmp_path, harness=None)
    assert fmconfig.read_harness(tmp_path).absent


# ---- fail-safe: merge -------------------------------------------------------------


@pytest.mark.parametrize(
    "projects,name",
    [
        (None, "alpha"),  # registry missing
        ("garbage only\n", "alpha"),  # registry unparsable
        (PROJECTS, "unlisted"),  # project not registered
        ("- a [bogus +yolo] - x\n", "a"),  # unknown mode
        ("- a [no-mistakes +yolo] - x\n- a [no-mistakes +yolo] - y\n", "a"),  # duplicate
    ],
)
def test_unknown_or_ambiguous_posture_reads_unknown(
    tmp_path: Path, projects: str | None, name: str
) -> None:
    p = fmconfig.read_projects(write_config(tmp_path, projects=projects))
    assert fmconfig.posture_label(p, name).startswith("unknown (")


def test_posture_label_relays_the_registry(tmp_path: Path) -> None:
    p = fmconfig.read_projects(write_config(tmp_path))
    assert fmconfig.posture_label(p, "alpha") == "no-mistakes +yolo"
    assert fmconfig.posture_label(p, "beta") == "direct-PR"


def test_merge_word_note_carries_posture_and_never_claims_self_merge(
    client: TestClient,
    home: Path,
) -> None:
    res = post(client, "merge", task="alpha-build")
    done = post(client, "merge", task="alpha-build", step=_hidden(res.text, "step"))
    assert done.status_code == 200
    (call,) = _calls(home)
    assert "\nposture: no-mistakes +yolo\n" in call["stdin"]
    assert "\nself-merge: no (the website cannot see checks; firstmate decides)\n" in call["stdin"]


@pytest.mark.parametrize("projects", [None, "garbage\n", "- alpha [bogus] - x\n"])
def test_merge_word_note_says_unknown_when_the_posture_is_unreadable(
    client: TestClient,
    home: Path,
    projects: str | None,
) -> None:
    write_config(home, projects=projects)
    res = post(client, "merge", task="alpha-build")
    assert "Merge posture: unknown (" in client.get("/p/alpha", headers=GOOD).text
    done = post(client, "merge", task="alpha-build", step=_hidden(res.text, "step"))
    (call,) = _calls(home)
    assert "\nposture: unknown (" in call["stdin"] and "\nself-merge: no (" in call["stdin"]
    assert done.status_code == 200  # the captain's own word is still recorded


# ---- fail-safe: workers -----------------------------------------------------------

BAD_WORKER_CONFIGS = {
    "dispatch unparsable": {"dispatch": "{nope"},
    "dispatch empty": {"dispatch": "{}"},
    "dispatch and harness missing": {"dispatch": None, "harness": None},
    "dispatch missing, harness malformed": {"dispatch": None, "harness": "a b\n"},
    "permission invalid": {"permission": "yolo"},
}


def _delegate(client: TestClient):
    return post(client, "delegate", text="Ship the docs", project="alpha")


def _ticket_create(client: TestClient):
    return post(client, "ticket", mode="create", project="alpha", title="New", text="")


@pytest.mark.parametrize("make", [_delegate, _ticket_create])
@pytest.mark.parametrize("name", sorted(BAD_WORKER_CONFIGS))
def test_no_worker_request_is_sent_without_usable_worker_settings(
    client: TestClient,
    home: Path,
    make,
    name: str,
) -> None:
    write_config(home, **BAD_WORKER_CONFIGS[name])
    res = make(client)
    assert res.status_code == 409
    assert "Ask the captain" in res.text and "will not pick a harness or model" in res.text
    assert _calls(home) == []  # nothing reached the first mate


def test_plain_note_to_the_first_mate_is_not_gated(client: TestClient, home: Path) -> None:
    write_config(home, dispatch="{nope")
    assert post(client, "delegate", text="Fix your crew-dispatch.json").status_code == 200
    assert len(_calls(home)) == 1


@pytest.mark.parametrize("make", [_delegate, _ticket_create])
@pytest.mark.parametrize(
    "cfg",
    [
        {},
        {"dispatch": None},  # no dispatch file: crew-harness alone, as firstmate does
        {"permission": "auto"},
        {"dispatch": {"default": {"harness": "codex"}}},
    ],
)
def test_worker_requests_go_through_with_usable_settings(
    client: TestClient,
    home: Path,
    make,
    cfg: dict,
) -> None:
    write_config(home, **cfg)
    assert make(client).status_code == 200
    assert len(_calls(home)) == 1


def test_worker_gate_is_read_on_every_request(client: TestClient, home: Path) -> None:
    write_config(home, dispatch="{nope")
    assert _delegate(client).status_code == 409
    write_config(home)
    assert _delegate(client).status_code == 200


def test_no_worker_defaults_live_in_hive(tmp_path: Path) -> None:
    """With no config at all the gate refuses; there is no built-in harness to fall back to."""
    cfg = fmconfig.load(write_config(tmp_path, dispatch=None, harness=None))
    assert cfg.workers_ready


# ---- desk view --------------------------------------------------------------------


def test_config_page_renders_postures_and_profiles(client: TestClient) -> None:
    res = client.get("/config", headers=GOOD)
    assert res.status_code == 200
    html = res.text
    for want in (
        "Merge posture",
        "<td>alpha</td><td>no-mistakes</td><td>on</td>",
        "<td>beta</td><td>direct-PR</td><td>off</td>",
        "claude · claude-opus-x · medium",
        "codex · gpt-x · high",
        "claude · claude-sonnet-x · default effort",
        "Anything else (default)",
        "codex · gpt-y · medium",
        "Crew harness: codex",
        "Claude permission mode: bypass (default)",
        "Workers can start from these settings.",
    ):
        assert want in html, want
    assert "href='/config'" in html  # the nav entry


@pytest.mark.parametrize(
    "cfg,expect",
    [
        ({"projects": None}, "unknown: projects.md not found"),
        ({"projects": "- a [bogus] - x\n"}, "unknown mode"),
        ({"dispatch": "{nope"}, "unknown: crew-dispatch.json unparsable"),
        ({"dispatch": None, "harness": None}, "unknown: crew-dispatch.json not found"),
        ({"harness": "a b"}, "crew-harness must hold one adapter name"),
        ({"permission": "yolo"}, "claude-permission-mode must be one of"),
    ],
)
def test_config_page_shows_unknown_with_the_reason_and_never_crashes(
    client: TestClient,
    home: Path,
    cfg: dict,
    expect: str,
) -> None:
    write_config(home, **cfg)
    res = client.get("/config", headers=GOOD)
    assert res.status_code == 200 and expect in res.text


def test_config_page_survives_a_missing_firstmate_home(tmp_path: Path) -> None:
    from hive.gateway.app import create_app
    from hive.gateway.settings import GatewaySettings
    from tests.gateway.test_actions import HOST

    app = create_app(
        GatewaySettings(owner_login=OWNER, allowed_hosts=(HOST,), fm_home=tmp_path / "nope")
    )
    res = TestClient(app, client=("127.0.0.1", 5000)).get("/config", headers=GOOD)
    assert (
        res.status_code == 200
        and res.text.count("unknown:") >= 2
        and "Crew harness: not set" in res.text
    )
    assert "Workers will not start" in res.text


def test_config_page_escapes_config_text(client: TestClient, home: Path) -> None:
    bad = {"rules": [{"when": "<script>x</script>", "use": {"harness": "<b>h</b>"}}]}
    write_config(home, dispatch=bad)
    html = client.get("/config", headers=GOOD).text
    assert "<script>x</script>" not in html and "<b>h</b>" not in html


def test_config_page_is_owner_only(client: TestClient) -> None:
    assert client.get("/config", headers={"host": GOOD["host"]}).status_code == 403
    assert client.post("/config", headers=GOOD).status_code == 405


def test_desk_merge_card_shows_the_posture(client: TestClient, home: Path) -> None:
    assert "Merge posture: no-mistakes +yolo" in client.get("/p/alpha", headers=GOOD).text
    write_config(home, projects=None)
    assert "Merge posture: unknown (" in client.get("/p/alpha", headers=GOOD).text
