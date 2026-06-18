"""Tests for entity/team name validation (Ticket 032)."""

from __future__ import annotations

import pytest

from hive.process.names import MAX_NAME_LEN, validate_name

# --- accept: valid names raise nothing -------------------------------------

VALID = [
    "otter",  # the PA maestro
    "dev",
    "hive_dev",  # underscore (the 030 trigger)
    "my-team",  # hyphen
    "Otter",  # case preserved
    "a",  # single char
    "A1_b-2",  # mixed allowed set
    "x" * MAX_NAME_LEN,  # exactly at the cap
]


@pytest.mark.parametrize("name", VALID)
def test_valid_names_pass(name):
    assert validate_name(name, kind="team name") is None


# --- reject: each boundary case raises ValueError --------------------------

INVALID = [
    "",  # empty
    "   ",  # whitespace only
    "my team",  # space
    "a/b",  # slash (path separator)
    ".",  # dot
    "..",  # parent dir (path traversal)
    "back.end",  # dot inside a component → breaks maestro.team addressing
    "-rf",  # leading dash (looks like a CLI flag)
    "x" * (MAX_NAME_LEN + 1),  # over the length cap
    "a;b",  # shell metacharacter
    "a$b",  # shell metacharacter
    "a b\tc",  # tab
    "tab\tname",
    "emoji😀",  # non-ASCII
]


@pytest.mark.parametrize("name", INVALID)
def test_invalid_names_rejected(name):
    with pytest.raises(ValueError):
        validate_name(name, kind="team name")


# --- message quality: actionable + names the kind --------------------------


def test_message_includes_kind():
    with pytest.raises(ValueError, match="maestro name"):
        validate_name("bad name", kind="maestro name")


def test_message_names_offending_char():
    with pytest.raises(ValueError, match=r"' '"):  # the space is quoted
        validate_name("my team", kind="team name")


def test_leading_dash_message_is_specific():
    with pytest.raises(ValueError, match="cannot start with"):
        validate_name("-x", kind="team name")


def test_empty_message_is_specific():
    with pytest.raises(ValueError, match="cannot be empty"):
        validate_name("", kind="team name")


def test_too_long_message_is_specific():
    with pytest.raises(ValueError, match="too long"):
        validate_name("z" * (MAX_NAME_LEN + 1), kind="team name")


def test_default_kind_is_name():
    with pytest.raises(ValueError, match="Invalid name"):
        validate_name("bad/name")
