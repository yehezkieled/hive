"""Structured decision cards, project descriptions in the card sheet, and the alerts menu."""

from __future__ import annotations

import json
from pathlib import Path

from hive.gateway.cards import parse_card
from hive.gateway.snapshot import project_descriptions, project_notes
from tests.gateway.test_stack_home import GOOD, _calm, _client, _rec, _snapshot

STRUCTURED = (
    "Should the desk hide merged PRs? Options: A) hide them B) show greyed out "
    "C) add a toggle. Recommend B because it is least surprising."
)


def test_structured_text_gives_question_options_and_recommendation() -> None:
    card = parse_card(STRUCTURED)
    assert card.question == "Should the desk hide merged PRs?"
    assert [(o.letter, o.label, o.recommended) for o in card.options] == [
        ("A", "hide them", False),
        ("B", "show greyed out", True),
        ("C", "add a toggle", False),
    ]
    assert card.more == STRUCTURED  # nothing dropped


def test_parenthesised_letters_and_recommended_colon() -> None:
    card = parse_card("Which harness? (A) Codex (B) Claude Code, recommended: A")
    assert [o.letter for o in card.options] == ["A", "B"]
    assert card.options[0].recommended and not card.options[1].recommended


def test_unstructured_text_is_first_sentence_plus_more() -> None:
    text = "This is a long paragraph. It goes on. And on."
    card = parse_card(text)
    assert card.question == "This is a long paragraph."
    assert card.options == []
    assert card.more == text


def test_short_or_empty_text_needs_no_more() -> None:
    assert parse_card("Short?").more == ""
    assert parse_card("").question == ""
    assert parse_card(None).question == ""  # type: ignore[arg-type]


def test_long_option_labels_are_shortened_but_kept_in_more() -> None:
    long = "one two three four five six seven eight nine ten eleven"
    card = parse_card(f"Pick? A) {long} B) short")
    assert card.options[0].label.endswith("…")
    assert long in card.more


def _decision_data() -> dict:
    data = _calm()
    data["backlog"]["records"].append(
        _rec("b2", "beta", "queued", hold_reason=STRUCTURED, captain_actionable=True)
    )
    return data


def test_decision_card_renders_structure_and_more(tmp_path: Path) -> None:
    html = _client(tmp_path, _decision_data()).get("/", headers=GOOD).text
    assert "<span class=dq__q>Should the desk hide merged PRs?</span>" in html
    assert "<li class='dq__opt is-rec'><b>B</b> show greyed out" in html
    assert "<details class=dq__more><summary>More</summary><p>Should the desk" in html
    assert STRUCTURED in html


def _mates(tmp_path: Path, charter: str | None) -> dict:
    home = tmp_path / "mate"
    (home / "data").mkdir(parents=True)
    (home / "data" / "projects.md").write_text("- hive [x] - own line (added 2026)\n")
    if charter is not None:
        (home / "data" / "charter.md").write_text(charter)
    return {
        "tasks": [
            {"id": "hive", "kind": "secondmate", "secondmate_projects": ["hive"]},
        ],
        "secondmate_current": {"records": [{"id": "hive", "home": str(home), "queued": []}]},
    }


def test_descriptions_from_registry_and_charter(tmp_path: Path) -> None:
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "projects.md").write_text(
        "- alpha [yolo] - the alpha thing (added 2026-09-29)\n- hive - registry hive\n"
    )
    data = _mates(tmp_path, "# Charter\nOwn the hive project end to end. More detail.\n\n# Next\nx")
    notes = project_descriptions(tmp_path, data)
    assert notes["alpha"] == "the alpha thing"
    assert notes["hive"] == "Own the hive project end to end."


def test_descriptions_missing_or_malformed_are_empty(tmp_path: Path) -> None:
    assert project_descriptions(tmp_path, {}) == {}
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "projects.md").write_bytes(b"\xff\xfe garbage\n- \n")
    assert project_descriptions(tmp_path, _mates(tmp_path, "no heading here")) == {}
    assert project_notes(type("S", (), {"data": {"project_descriptions": "bad"}})()) == {}


def test_card_sheet_shows_description_when_known(tmp_path: Path) -> None:
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "projects.md").write_text("- alpha - the alpha thing\n")
    html = _client(tmp_path, _calm()).get("/", headers=GOOD).text
    assert "<p class=sheet__about>the alpha thing</p>" in html
    assert html.count("sheet__about>") == 1  # projects without a description show no line


def test_no_description_source_is_not_an_error(tmp_path: Path) -> None:
    res = _client(tmp_path, _calm()).get("/", headers=GOOD)
    assert res.status_code == 200 and "<p class=sheet__about>" not in res.text


def test_alerts_tip_lives_in_the_usage_menu(tmp_path: Path) -> None:
    html = _client(tmp_path, _snapshot()).get("/", headers=GOOD).text
    qpop = html.split("id=qchip", 1)[1].split("</details>", 1)[0]
    assert "Get alerts" in qpop and "Add to Home Screen" in qpop and "id=alerts-note" in qpop
    assert html.count("id=alerts-note") == 1
    assert "stamp id=alerts-note" not in html
    json.dumps(html)  # rendered, not an error page
