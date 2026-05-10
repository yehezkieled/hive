"""Tests for the dashboard-aggregation methods on TokenStore."""

from datetime import UTC, datetime, timedelta

from hive.bus.token_store import TokenStore

# --- daily_cost ---


async def test_daily_cost_zero_filled(token_store: TokenStore) -> None:
    rows = await token_store.daily_cost(days=7)
    assert len(rows) == 7
    assert all(r["cost"] == 0.0 for r in rows)
    # Date strings are unique and in YYYY-MM-DD form
    dates = [r["date"] for r in rows]
    assert len(set(dates)) == 7
    assert all(len(d) == 10 and d.count("-") == 2 for d in dates)


async def test_daily_cost_groups_by_day(token_store: TokenStore) -> None:
    pool = token_store.pool
    today = datetime.now(UTC)
    yesterday = today - timedelta(days=1)

    await pool.execute(
        """INSERT INTO token_usage (entity_name, model, cost_usd, recorded_at)
           VALUES ($1, $2, $3, $4)""",
        "dev",
        "sonnet",
        0.10,
        today,
    )
    await pool.execute(
        """INSERT INTO token_usage (entity_name, model, cost_usd, recorded_at)
           VALUES ($1, $2, $3, $4)""",
        "dev",
        "sonnet",
        0.20,
        today,
    )
    await pool.execute(
        """INSERT INTO token_usage (entity_name, model, cost_usd, recorded_at)
           VALUES ($1, $2, $3, $4)""",
        "dev",
        "sonnet",
        0.05,
        yesterday,
    )

    rows = await token_store.daily_cost(days=7)
    by_date = {r["date"]: r["cost"] for r in rows}
    today_str = today.strftime("%Y-%m-%d")
    yesterday_str = yesterday.strftime("%Y-%m-%d")
    assert round(by_date[today_str], 2) == 0.30
    assert round(by_date[yesterday_str], 2) == 0.05


async def test_daily_cost_includes_dow(token_store: TokenStore) -> None:
    rows = await token_store.daily_cost(days=7)
    for r in rows:
        assert isinstance(r["dow"], int)
        assert 0 <= r["dow"] <= 6


async def test_daily_cost_excludes_outside_window(token_store: TokenStore) -> None:
    pool = token_store.pool
    long_ago = datetime.now(UTC) - timedelta(days=14)
    await pool.execute(
        """INSERT INTO token_usage (entity_name, model, cost_usd, recorded_at)
           VALUES ($1, $2, $3, $4)""",
        "dev",
        "sonnet",
        99.99,
        long_ago,
    )

    rows = await token_store.daily_cost(days=7)
    assert all(r["cost"] == 0.0 for r in rows)


# --- token_burn ---


async def test_token_burn_zero_filled(token_store: TokenStore) -> None:
    rows = await token_store.token_burn(window=timedelta(hours=1), buckets=4)
    assert len(rows) == 4
    for r in rows:
        assert r["input_tokens"] == 0
        assert r["output_tokens"] == 0
        assert r["cache_creation_input_tokens"] == 0
        assert r["cache_read_input_tokens"] == 0
        assert r["cost"] == 0.0
    assert [r["i"] for r in rows] == [0, 1, 2, 3]


async def test_token_burn_aggregates_totals(token_store: TokenStore) -> None:
    pool = token_store.pool
    for ip, op, cc, cr, cost in [
        (100, 50, 10, 5, 0.10),
        (200, 80, 20, 15, 0.15),
    ]:
        await pool.execute(
            """INSERT INTO token_usage (entity_name, model, input_tokens,
               output_tokens, cache_creation_input_tokens,
               cache_read_input_tokens, cost_usd)
               VALUES ($1, $2, $3, $4, $5, $6, $7)""",
            "dev",
            "sonnet",
            ip,
            op,
            cc,
            cr,
            cost,
        )

    rows = await token_store.token_burn(window=timedelta(hours=1), buckets=4)
    assert sum(r["input_tokens"] for r in rows) == 300
    assert sum(r["output_tokens"] for r in rows) == 130
    assert sum(r["cache_creation_input_tokens"] for r in rows) == 30
    assert sum(r["cache_read_input_tokens"] for r in rows) == 20
    assert round(sum(r["cost"] for r in rows), 2) == 0.25


async def test_token_burn_excludes_outside_window(token_store: TokenStore) -> None:
    pool = token_store.pool
    long_ago = datetime.now(UTC) - timedelta(hours=5)
    await pool.execute(
        """INSERT INTO token_usage (entity_name, model, input_tokens,
           output_tokens, cost_usd, recorded_at)
           VALUES ($1, $2, $3, $4, $5, $6)""",
        "dev",
        "sonnet",
        500,
        500,
        9.99,
        long_ago,
    )

    rows = await token_store.token_burn(window=timedelta(hours=1), buckets=4)
    assert sum(r["input_tokens"] for r in rows) == 0
    assert sum(r["cost"] for r in rows) == 0.0


# --- cost_by_entity_model ---


async def test_cost_by_entity_model_empty(token_store: TokenStore) -> None:
    since = datetime.now(UTC) - timedelta(hours=1)
    out = await token_store.cost_by_entity_model(since)
    assert out == {}


async def test_cost_by_entity_model_groups(token_store: TokenStore) -> None:
    pool = token_store.pool
    for entity, model, cost in [
        ("dev", "sonnet", 0.10),
        ("dev", "sonnet", 0.05),
        ("dev", "haiku", 0.01),
        ("otter", "opus", 0.20),
    ]:
        await pool.execute(
            """INSERT INTO token_usage (entity_name, model, cost_usd)
               VALUES ($1, $2, $3)""",
            entity,
            model,
            cost,
        )

    since = datetime.now(UTC) - timedelta(hours=1)
    out = await token_store.cost_by_entity_model(since)
    assert set(out.keys()) == {"dev", "otter"}
    assert round(out["dev"]["sonnet"], 2) == 0.15
    assert round(out["dev"]["haiku"], 2) == 0.01
    assert round(out["otter"]["opus"], 2) == 0.20
    assert "haiku" not in out["otter"]


async def test_cost_by_entity_model_filters_since(token_store: TokenStore) -> None:
    pool = token_store.pool
    await pool.execute(
        """INSERT INTO token_usage (entity_name, model, cost_usd, recorded_at)
           VALUES ($1, $2, $3, $4)""",
        "dev",
        "sonnet",
        0.10,
        datetime.now(UTC) - timedelta(days=2),
    )

    since = datetime.now(UTC) - timedelta(hours=1)
    out = await token_store.cost_by_entity_model(since)
    assert out == {}


# --- cache_stats ---


async def test_cache_stats_empty(token_store: TokenStore) -> None:
    since = datetime.now(UTC) - timedelta(hours=1)
    out = await token_store.cache_stats(since)
    assert out == []


async def test_cache_stats_hit_pct(token_store: TokenStore) -> None:
    pool = token_store.pool
    await pool.execute(
        """INSERT INTO token_usage (entity_name, model, input_tokens,
           cache_read_input_tokens) VALUES ($1, $2, $3, $4)""",
        "dev",
        "sonnet",
        100,
        300,
    )

    since = datetime.now(UTC) - timedelta(hours=1)
    out = await token_store.cache_stats(since)
    assert len(out) == 1
    assert out[0]["name"] == "dev"
    assert out[0]["cached_tokens"] == 300
    assert out[0]["fresh_tokens"] == 100
    assert out[0]["hit_pct"] == 75.0


async def test_cache_stats_per_entity(token_store: TokenStore) -> None:
    pool = token_store.pool
    for entity, ip, cr in [
        ("dev", 100, 100),  # 50%
        ("otter", 200, 0),  # 0%
    ]:
        await pool.execute(
            """INSERT INTO token_usage (entity_name, model, input_tokens,
               cache_read_input_tokens) VALUES ($1, $2, $3, $4)""",
            entity,
            "sonnet",
            ip,
            cr,
        )

    since = datetime.now(UTC) - timedelta(hours=1)
    out = await token_store.cache_stats(since)
    by_name = {r["name"]: r for r in out}
    assert by_name["dev"]["hit_pct"] == 50.0
    assert by_name["otter"]["hit_pct"] == 0.0


async def test_cache_stats_excludes_outside_window(token_store: TokenStore) -> None:
    pool = token_store.pool
    await pool.execute(
        """INSERT INTO token_usage (entity_name, model, input_tokens,
           cache_read_input_tokens, recorded_at) VALUES ($1, $2, $3, $4, $5)""",
        "dev",
        "sonnet",
        100,
        300,
        datetime.now(UTC) - timedelta(days=2),
    )

    since = datetime.now(UTC) - timedelta(hours=1)
    out = await token_store.cache_stats(since)
    assert out == []


# --- cache_overall_daily ---


async def test_cache_overall_daily_zero_filled(token_store: TokenStore) -> None:
    out = await token_store.cache_overall_daily(days=7)
    assert len(out) == 7
    assert all(v == 0.0 for v in out)


async def test_cache_overall_daily_today(token_store: TokenStore) -> None:
    pool = token_store.pool
    # 90% hit-rate today: cache=900, fresh=100
    await pool.execute(
        """INSERT INTO token_usage (entity_name, model, input_tokens,
           cache_read_input_tokens) VALUES ($1, $2, $3, $4)""",
        "dev",
        "sonnet",
        100,
        900,
    )

    out = await token_store.cache_overall_daily(days=7)
    # Last entry is today, others zero
    assert out[-1] == 90.0
    assert all(v == 0.0 for v in out[:-1])


# --- cache_baseline_7d ---


async def test_cache_baseline_7d_empty_input(token_store: TokenStore) -> None:
    assert await token_store.cache_baseline_7d([]) == {}


async def test_cache_baseline_7d_no_history(token_store: TokenStore) -> None:
    # Entity is requested but has zero rows in the 7-day window — omitted from
    # the result so the view-model falls back to the current-window hit rate.
    assert await token_store.cache_baseline_7d(["dev"]) == {}


async def test_cache_baseline_7d_averages_window(token_store: TokenStore) -> None:
    pool = token_store.pool
    # 7 days of usage at a stable 80% hit rate (cached=400, fresh=100 per day).
    for d in range(7):
        await pool.execute(
            """INSERT INTO token_usage (entity_name, model, input_tokens,
               cache_read_input_tokens, recorded_at) VALUES ($1, $2, $3, $4, $5)""",
            "dev",
            "sonnet",
            100,
            400,
            datetime.now(UTC) - timedelta(days=d, hours=2),
        )

    out = await token_store.cache_baseline_7d(["dev"])
    assert out == {"dev": 80.0}


async def test_cache_baseline_7d_filters_to_requested_entities(
    token_store: TokenStore,
) -> None:
    pool = token_store.pool
    for entity, ip, cr in [("dev", 100, 400), ("otter", 200, 0)]:
        await pool.execute(
            """INSERT INTO token_usage (entity_name, model, input_tokens,
               cache_read_input_tokens) VALUES ($1, $2, $3, $4)""",
            entity,
            "sonnet",
            ip,
            cr,
        )

    out = await token_store.cache_baseline_7d(["dev"])
    assert out == {"dev": 80.0}
    assert "otter" not in out


async def test_cache_baseline_7d_excludes_outside_window(
    token_store: TokenStore,
) -> None:
    pool = token_store.pool
    # 8-day-old usage falls outside the 7-day window.
    await pool.execute(
        """INSERT INTO token_usage (entity_name, model, input_tokens,
           cache_read_input_tokens, recorded_at) VALUES ($1, $2, $3, $4, $5)""",
        "dev",
        "sonnet",
        100,
        400,
        datetime.now(UTC) - timedelta(days=8),
    )

    assert await token_store.cache_baseline_7d(["dev"]) == {}
