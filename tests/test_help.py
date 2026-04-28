"""Tests for /help command — help_text module and drift prevention."""

from hive.telegram.bridge import BRIDGE_COMMANDS
from hive.telegram.commands import parse_command
from hive.telegram.help_text import (
    CATEGORIES,
    HELP_TEXT,
    HelpEntry,
    format_all,
    format_one,
)


def test_every_bridge_command_has_help_entry() -> None:
    """Drift guard: every command dispatched in bridge.py needs a HELP_TEXT entry."""
    missing = BRIDGE_COMMANDS - set(HELP_TEXT.keys())
    assert not missing, f"Commands without HELP_TEXT entries: {sorted(missing)}"


def test_every_help_entry_is_a_real_command() -> None:
    """Drift guard: no dead entries in HELP_TEXT for commands that got removed."""
    orphaned = set(HELP_TEXT.keys()) - BRIDGE_COMMANDS
    assert not orphaned, f"HELP_TEXT entries with no bridge handler: {sorted(orphaned)}"


def test_all_entries_use_known_categories() -> None:
    """Every entry's category must appear in CATEGORIES (order drives /help output)."""
    unknown = {e.category for e in HELP_TEXT.values()} - set(CATEGORIES)
    assert not unknown, f"Categories used but not in CATEGORIES tuple: {unknown}"


def test_all_entries_have_required_fields() -> None:
    for name, entry in HELP_TEXT.items():
        assert isinstance(entry, HelpEntry), f"{name} is not a HelpEntry"
        assert entry.category, f"{name} has empty category"
        assert entry.usage, f"{name} has empty usage"
        assert entry.description, f"{name} has empty description"


def test_format_all_includes_every_command() -> None:
    rendered = format_all()
    for name, entry in HELP_TEXT.items():
        shown = entry.display or name
        assert f"/{shown}" in rendered, f"{name} missing from /help output"


def test_format_all_groups_by_category() -> None:
    rendered = format_all()
    for category in CATEGORIES:
        if any(e.category == category for e in HELP_TEXT.values()):
            assert category in rendered, f"category header missing: {category}"


def test_format_all_fits_in_one_telegram_message() -> None:
    """Telegram hard cap is 4096 chars per message. Warn well before that."""
    rendered = format_all()
    assert len(rendered) < 4096, (
        f"/help output is {len(rendered)} chars — will fit but leaves little headroom"
    )


def test_format_one_returns_detail() -> None:
    rendered = format_one("vault")
    assert "/vault" in rendered
    assert "approve" in rendered
    assert HELP_TEXT["vault"].description in rendered


def test_format_one_strips_leading_slash() -> None:
    with_slash = format_one("/vault")
    without = format_one("vault")
    assert with_slash == without


def test_format_one_is_case_insensitive() -> None:
    assert format_one("VAULT") == format_one("vault")


def test_format_one_unknown_command() -> None:
    rendered = format_one("nonexistent")
    assert "Unknown" in rendered
    assert "/help" in rendered


def test_examples_render_when_present() -> None:
    rendered = format_one("task")
    for example in HELP_TEXT["task"].examples:
        assert example in rendered


def test_help_parses_with_target() -> None:
    """`/help vault` should parse with target='vault' so the handler can render detail."""
    cmd = parse_command("/help vault")
    assert cmd.name == "help"
    assert cmd.target == "vault"


def test_help_parses_without_target() -> None:
    cmd = parse_command("/help")
    assert cmd.name == "help"
    assert cmd.target is None


def test_every_entry_has_examples() -> None:
    """Every HelpEntry must have at least one example."""
    missing = [name for name, entry in HELP_TEXT.items() if not entry.examples]
    assert missing == [], f"Entries missing examples: {missing}"


def test_description_style() -> None:
    """Every description must: start with capital letter, be ≤80 chars, end with period."""
    errors = []
    for name, entry in HELP_TEXT.items():
        desc = entry.description
        if not desc[0].isupper():
            errors.append(f"/{name}: description must start with capital letter")
        if len(desc) > 80:
            errors.append(f"/{name}: description too long ({len(desc)} chars > 80)")
        if not desc.endswith("."):
            errors.append(f"/{name}: description must end with period")
    assert errors == [], "\n".join(errors)


def test_categories_alphabetical_within() -> None:
    """Commands within each category should be listed alphabetically."""
    for category in CATEGORIES:
        names = [name for name, e in HELP_TEXT.items() if e.category == category]
        assert names == sorted(names), f"{category}: not alphabetical: {names}"
