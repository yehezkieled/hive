"""Classify a task ``failure_reason`` string into a coarse category.

Powers the W8 failure-scatter dashboard widget. The categories are
deliberately coarse (6 buckets) so the scatter shape stays readable —
fine-grained classification belongs to the audit log, not the
dashboard view.
"""

from __future__ import annotations

import re

CATEGORIES = ("timeout", "rate_limit", "syntax", "permission", "network", "unknown")

_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("timeout", re.compile(r"\b(timeout|timed?\s*out|deadline\s*exceeded)\b", re.I)),
    ("rate_limit", re.compile(r"\b(rate.?limit|429|too\s+many\s+requests|quota)\b", re.I)),
    (
        "syntax",
        re.compile(
            r"(syntax\w*|parse\s*error|invalid\s*format|malformed|json\s*decode)",
            re.I,
        ),
    ),
    ("permission", re.compile(r"\b(permission|forbidden|unauthori[sz]ed|403|401)\b", re.I)),
    ("network", re.compile(r"\b(connection|dns|network|unreachable|socket|reset)\b", re.I)),
)


def classify(reason: str | None) -> str:
    """Map a free-text failure reason into one of ``CATEGORIES``.

    Empty/None reasons return ``"unknown"`` so callers can group rows
    that lost their reason field. Order matters: the more specific
    patterns (timeout, rate_limit) win over the broader ones (network).
    """
    if not reason:
        return "unknown"
    for category, pattern in _PATTERNS:
        if pattern.search(reason):
            return category
    return "unknown"
