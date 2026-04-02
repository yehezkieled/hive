"""Shared test fixtures for Hive."""

from pathlib import Path

import pytest


@pytest.fixture
def tmp_data_dir(tmp_path: Path) -> Path:
    """Temporary directory for test data (SQLite, logs, etc.)."""
    return tmp_path


@pytest.fixture
def personalities_dir(tmp_path: Path) -> Path:
    """Temporary directory with test personality files."""
    d = tmp_path / "personalities"
    d.mkdir()

    template = d / "maestro-dev.md"
    template.write_text(
        """# Maestro: Dev

## Identity
- **Name**: Dev
- **Role**: maestro
- **Model**: sonnet

## System Prompt
You are Dev, a software engineering maestro. You lead development teams
and coordinate technical work.

## Tools
- allowedTools: Bash Read Write Edit Grep Glob
"""
    )
    return d
