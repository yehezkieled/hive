"""Failure classification from the harnesses' real error text (ADR 0029).

The "real" samples were captured from the installed binaries on 2026-10-01
(Claude Code 2.1.286 logged out via an empty CLAUDE_CONFIG_DIR; Pi 0.87 with no
provider configured). Add a sample here whenever a harness words an error anew.
"""

import pytest

from hive.runtime.harness import HarnessErrorKind as K
from hive.runtime.headless import classify_failure_text, parse_jsonl


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        # --- captured from real runs ---
        ("Not logged in · Please run /login", K.AUTH),  # claude -p, logged out
        ("No API key found for the selected model.\n\nUse /login to log into a provider", K.AUTH),
        ("No models available. Use /login to log into a provider via OAuth or API key.", K.AUTH),
        ('{"status":"not_ready","reason":"credentials_not_configured"}', K.AUTH),
        # --- quota / credit exhaustion ---
        ("You've hit your limit · resets 5pm (UTC)", K.QUOTA),
        ("Claude usage limit reached. Your limit will reset at 3pm", K.QUOTA),
        ("429 Too Many Requests", K.QUOTA),
        ("Your credit balance is too low to access the API", K.QUOTA),
        ("You're out of extra usage for this month", K.QUOTA),
        # --- headless refused ---
        ("Headless usage is not included in your plan", K.REFUSED),
        ("print mode requires a separate monthly credit", K.REFUSED),
        # --- anything else must NOT be mistaken for a refusal ---
        ("Segmentation fault", K.OTHER),
        ("529 overloaded_error", K.OTHER),
        ("", K.OTHER),
    ],
)
def test_classify_failure_text(text: str, kind: K) -> None:
    assert classify_failure_text(text) is kind


def test_quota_text_mentioning_login_is_not_auth() -> None:
    assert classify_failure_text("usage limit reached — log in again to upgrade") is K.QUOTA


def test_parse_jsonl_skips_noise() -> None:
    text = 'warning: x\n{"type":"a"}\n\n{broken\n{"type":"b"}\n[1]\n'
    assert [e["type"] for e in parse_jsonl(text)] == ["a", "b"]
