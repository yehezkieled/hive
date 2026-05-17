"""Tests for the PTY output cleaner."""

from hive.runtime.output_parser import clean


def test_ansi_color_codes_are_stripped() -> None:
    raw = "\x1b[32mHello\x1b[0m, world!"
    assert clean(raw) == "Hello, world!"


def test_cursor_movement_sequences_are_stripped() -> None:
    # CSI A (up), CSI K (erase line), CSI 2J (clear screen)
    raw = "\x1b[1A\x1b[2K\x1b[2JDone."
    assert clean(raw) == "Done."


def test_carriage_return_rewrites_keep_final_overwrite() -> None:
    # Terminal progress bars overwrite lines via \r
    raw = "Progress: 10%\rProgress: 50%\rProgress: 100%\n"
    assert clean(raw) == "Progress: 100%\n"


def test_spinner_frames_are_dropped() -> None:
    raw = "⠙ Thinking...\nHere is my answer.\n⠹ Finishing up\n"
    assert clean(raw) == "Here is my answer.\n"


def test_claude_code_progress_lines_are_dropped() -> None:
    raw = (
        "✓ Bash(ls -la)\n"
        "✗ Read(missing.py)\n"
        "→ Write(out.txt)\n"
        "· Edit(file.py)\n"
        "  Bash(echo hi)\n"
        "The task is complete.\n"
    )
    assert clean(raw) == "The task is complete.\n"


def test_plain_prose_is_preserved() -> None:
    prose = "Here is my analysis:\n\n1. The code looks fine.\n2. Tests pass.\n"
    assert clean(prose) == prose
