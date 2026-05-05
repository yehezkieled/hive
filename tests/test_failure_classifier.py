"""Tests for the Sprint 24 W8 failure classifier."""

from __future__ import annotations

import pytest

from hive.observability.failure_classifier import CATEGORIES, classify


@pytest.mark.parametrize(
    "reason,expected",
    [
        ("Request timed out after 30s", "timeout"),
        ("ReadTimeoutError: connection deadline exceeded", "timeout"),
        ("HTTP 429 Too Many Requests", "rate_limit"),
        ("rate limit exceeded; please slow down", "rate_limit"),
        ("Quota exhausted for project foo", "rate_limit"),
        ("SyntaxError: invalid token at line 42", "syntax"),
        ("JSON decode error: Expecting value", "syntax"),
        ("malformed payload from upstream", "syntax"),
        ("Permission denied opening /etc/shadow", "permission"),
        ("403 Forbidden", "permission"),
        ("Unauthorized: token expired", "permission"),
        ("Connection refused (network unreachable)", "network"),
        ("DNS resolution failed for api.example.com", "network"),
        ("socket reset by peer", "network"),
        ("", "unknown"),
        (None, "unknown"),
        ("internal server error", "unknown"),
    ],
)
def test_classify(reason: str | None, expected: str) -> None:
    assert classify(reason) == expected


def test_categories_constant_has_all_six() -> None:
    assert set(CATEGORIES) == {
        "timeout",
        "rate_limit",
        "syntax",
        "permission",
        "network",
        "unknown",
    }
