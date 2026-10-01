"""Harness names and the per-Harness /model sets with their billing tags (ADR 0001).

A Harness decides billing, so the valid model set — and which models are
API-billed — is a property of the Harness, not of Hive globally. Claude Code's
set lives beside ``Entity`` (``VALID_MODELS`` / ``API_BILLED_MODELS``) and is
read lazily here so a test's monkeypatch of those names is honoured.
"""

from __future__ import annotations

CLAUDE_CODE = "claude-code"
CODEX = "codex"
HARNESSES: tuple[str, ...] = (CLAUDE_CODE, CODEX)
DEFAULT_HARNESS = CLAUDE_CODE

# Models the Codex CLI offers on a ChatGPT/Codex plan login (read from the
# installed CLI's model catalogue, ``~/.codex/models_cache.json``).
CODEX_MODELS: frozenset[str] = frozenset({"gpt-5.5", "gpt-5.6-luna", "gpt-5.6-terra", "gpt-6-luna"})

# Codex models billed per-token against an API key. Empty today: every model
# above runs on the plan login. Add a name here the moment an API-billed model
# is offered; ``/model`` already warns on members.
CODEX_API_BILLED_MODELS: frozenset[str] = frozenset()

_DEFAULT_MODELS: dict[str, str] = {CLAUDE_CODE: "opus", CODEX: "gpt-5.5"}


def validate_harness(harness: str) -> str:
    """Return ``harness`` if known, else raise ValueError listing the valid names."""
    if harness not in HARNESSES:
        raise ValueError(f"Unknown harness {harness!r}. Valid: {', '.join(HARNESSES)}")
    return harness


def valid_models(harness: str) -> frozenset[str]:
    """The model names ``/model`` accepts on ``harness``."""
    validate_harness(harness)
    if harness == CODEX:
        return CODEX_MODELS
    from hive.models import entity as entity_mod

    return entity_mod.VALID_MODELS


def api_billed_models(harness: str) -> frozenset[str]:
    """The API-billed (real money) subset of ``valid_models(harness)``."""
    validate_harness(harness)
    if harness == CODEX:
        return CODEX_API_BILLED_MODELS
    from hive.models import entity as entity_mod

    return entity_mod.API_BILLED_MODELS


def default_model(harness: str) -> str:
    """The model an entity gets when it is moved onto ``harness`` without one."""
    validate_harness(harness)
    return _DEFAULT_MODELS[harness]


def billing_warning(harness: str, model: str) -> str | None:
    """One-line billing warning when ``model`` is API-billed on ``harness``, else None."""
    if model in api_billed_models(harness):
        return f"⚠️  {model!r} is API-billed — this costs real money per token, not plan quota."
    return None
