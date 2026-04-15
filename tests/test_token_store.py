"""Tests for the asyncpg-backed TokenStore."""

from datetime import UTC, datetime, timedelta

from hive.bus.token_store import TokenStore


def _usage(
    input_tokens: int = 10,
    output_tokens: int = 20,
    cache_creation_input_tokens: int = 0,
    cache_read_input_tokens: int = 0,
    cost_usd: float | None = 0.01,
    session_id: str | None = "sess-1",
    model: str = "sonnet",
) -> dict:
    return {
        "session_id": session_id,
        "model": model,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_creation_input_tokens": cache_creation_input_tokens,
        "cache_read_input_tokens": cache_read_input_tokens,
        "cost_usd": cost_usd,
    }


async def test_record_returns_id(token_store: TokenStore) -> None:
    row_id = await token_store.record("dev", _usage())
    assert isinstance(row_id, int)
    assert row_id > 0


async def test_record_stores_all_fields(token_store: TokenStore) -> None:
    await token_store.record(
        "dev",
        _usage(
            input_tokens=9,
            output_tokens=53,
            cache_creation_input_tokens=31449,
            cache_read_input_tokens=0,
            cost_usd=0.03958525,
        ),
    )

    recent = await token_store.recent(limit=1)
    assert len(recent) == 1
    row = recent[0]
    assert row["entity_name"] == "dev"
    assert row["input_tokens"] == 9
    assert row["output_tokens"] == 53
    assert row["cache_creation_input_tokens"] == 31449
    assert row["cache_read_input_tokens"] == 0
    assert float(row["cost_usd"]) == 0.03958525
    assert row["session_id"] == "sess-1"
    assert row["model"] == "sonnet"


async def test_totals_empty(token_store: TokenStore) -> None:
    totals = await token_store.totals()
    assert totals["call_count"] == 0
    assert totals["input_tokens"] == 0
    assert totals["output_tokens"] == 0
    assert float(totals["cost_usd"]) == 0.0


async def test_totals_aggregates(token_store: TokenStore) -> None:
    await token_store.record("dev", _usage(input_tokens=10, output_tokens=20, cost_usd=0.01))
    await token_store.record("dev", _usage(input_tokens=5, output_tokens=15, cost_usd=0.02))
    await token_store.record("pa", _usage(input_tokens=3, output_tokens=4, cost_usd=0.03))

    totals = await token_store.totals()
    assert totals["call_count"] == 3
    assert totals["input_tokens"] == 18
    assert totals["output_tokens"] == 39
    assert float(totals["cost_usd"]) == 0.06


async def test_totals_filters_by_entity(token_store: TokenStore) -> None:
    await token_store.record("dev", _usage(input_tokens=10, cost_usd=0.01))
    await token_store.record("pa", _usage(input_tokens=100, cost_usd=0.05))

    dev_totals = await token_store.totals(entity_name="dev")
    assert dev_totals["call_count"] == 1
    assert dev_totals["input_tokens"] == 10
    assert float(dev_totals["cost_usd"]) == 0.01

    pa_totals = await token_store.totals(entity_name="pa")
    assert pa_totals["call_count"] == 1
    assert pa_totals["input_tokens"] == 100


async def test_totals_filters_by_since(token_store: TokenStore) -> None:
    await token_store.record("dev", _usage(input_tokens=7))

    # Cutoff in the future — nothing should match
    future = datetime.now(UTC) + timedelta(hours=1)
    totals = await token_store.totals(since=future)
    assert totals["call_count"] == 0

    # Cutoff in the past — everything should match
    past = datetime.now(UTC) - timedelta(hours=1)
    totals = await token_store.totals(since=past)
    assert totals["call_count"] == 1
    assert totals["input_tokens"] == 7


async def test_recent_returns_newest_first(token_store: TokenStore) -> None:
    await token_store.record("dev", _usage(input_tokens=1, session_id="a"))
    await token_store.record("dev", _usage(input_tokens=2, session_id="b"))
    await token_store.record("dev", _usage(input_tokens=3, session_id="c"))

    rows = await token_store.recent(limit=10)
    assert [r["session_id"] for r in rows] == ["c", "b", "a"]


async def test_record_with_null_cost_and_session(token_store: TokenStore) -> None:
    await token_store.record("dev", _usage(cost_usd=None, session_id=None))

    recent = await token_store.recent(limit=1)
    assert recent[0]["cost_usd"] is None
    assert recent[0]["session_id"] is None


async def test_record_defaults_missing_fields(token_store: TokenStore) -> None:
    """Missing token-count keys should default to 0 rather than raise."""
    await token_store.record("dev", {"model": "haiku"})

    recent = await token_store.recent(limit=1)
    row = recent[0]
    assert row["input_tokens"] == 0
    assert row["output_tokens"] == 0
    assert row["cache_creation_input_tokens"] == 0
    assert row["cache_read_input_tokens"] == 0
    assert row["model"] == "haiku"
