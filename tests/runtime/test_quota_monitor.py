"""Tests for QuotaMonitor — plan-quota poller and alert dispatcher."""

from __future__ import annotations

import asyncio
import json
import urllib.error
from dataclasses import fields
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from hive.notifications.dispatcher import NotificationDispatcher
from hive.runtime.quota_monitor import (
    QuotaMonitor,
    QuotaReading,
    WindowReading,
    format_quota_text,
)


def _write_credentials(path: Path, token: str = "test-token-123") -> Path:
    """Write a credentials.json with the given token. Returns the path."""
    path.write_text(json.dumps({"claudeAiOauth": {"accessToken": token}}))
    return path


def _success_response(
    *,
    five_hour_util: float = 50.0,
    five_hour_resets: str = "2026-05-20T14:00:00+00:00",
    seven_day_util: float = 30.0,
    seven_day_resets: str = "2026-05-22T15:00:00+00:00",
) -> dict:
    """Build a sample successful API response."""
    return {
        "five_hour": {"utilization": five_hour_util, "resets_at": five_hour_resets},
        "seven_day": {"utilization": seven_day_util, "resets_at": seven_day_resets},
    }


def _make_monitor_with_recorder(tmp_path: Path, fetch: AsyncMock, **kwargs):
    """Construct a QuotaMonitor wired to a recording channel.

    Returns (monitor, sent_notifications_list).
    """
    creds = _write_credentials(tmp_path / "credentials.json")
    dispatcher = NotificationDispatcher()
    sent: list = []

    class _Recorder:
        async def send(self, notification):
            sent.append(notification)

    dispatcher.register(_Recorder())
    monitor = QuotaMonitor(
        credentials_path=creds,
        notifications=dispatcher,
        fetch_callable=fetch,
        **kwargs,
    )
    return monitor, sent


# --- Polling & parsing -------------------------------------------------------


async def test_poll_calls_endpoint_with_correct_auth_and_beta_header(tmp_path: Path):
    """Behavior 1: poll calls the endpoint with Bearer token + anthropic-beta header."""
    creds = _write_credentials(tmp_path / "credentials.json", token="test-token-123")
    fetch = AsyncMock(return_value=_success_response())
    monitor = QuotaMonitor(
        credentials_path=creds,
        notifications=NotificationDispatcher(),
        fetch_callable=fetch,
    )
    await monitor.poll_once()
    fetch.assert_called_once_with(
        "https://api.anthropic.com/api/oauth/usage",
        {
            "Authorization": "Bearer test-token-123",
            "anthropic-beta": "oauth-2025-04-20",
        },
    )


async def test_successful_response_parses_into_quota_reading(tmp_path: Path):
    """Behavior 2: parses five_hour + seven_day into a QuotaReading."""
    creds = _write_credentials(tmp_path / "credentials.json")
    fetch = AsyncMock(
        return_value=_success_response(
            five_hour_util=42.5,
            five_hour_resets="2026-05-20T14:00:00+00:00",
            seven_day_util=18.0,
            seven_day_resets="2026-05-22T15:00:00+00:00",
        )
    )
    monitor = QuotaMonitor(
        credentials_path=creds,
        notifications=NotificationDispatcher(),
        fetch_callable=fetch,
    )
    await monitor.poll_once()
    reading = monitor.get_quota()
    assert reading is not None
    assert reading.five_hour.utilization == 42.5
    assert reading.five_hour.resets_at == datetime(2026, 5, 20, 14, 0, tzinfo=UTC)
    assert reading.seven_day.utilization == 18.0
    assert reading.seven_day.resets_at == datetime(2026, 5, 22, 15, 0, tzinfo=UTC)


async def test_codename_and_per_model_keys_are_not_surfaced(tmp_path: Path):
    """Behavior 3: codename / per-model keys never leak into the reading."""
    creds = _write_credentials(tmp_path / "credentials.json")
    response = _success_response()
    response["seven_day_sonnet"] = {
        "utilization": 22.0,
        "resets_at": "2026-05-22T15:00:00+00:00",
    }
    response["seven_day_opus"] = None
    response["omelette"] = {"utilization": 99.0, "resets_at": None}
    response["tangelo"] = None
    response["iguana_necktie"] = None
    response["seven_day_cowork"] = None
    fetch = AsyncMock(return_value=response)
    monitor = QuotaMonitor(
        credentials_path=creds,
        notifications=NotificationDispatcher(),
        fetch_callable=fetch,
    )
    await monitor.poll_once()  # must not raise on the extra keys
    reading = monitor.get_quota()
    assert reading is not None
    assert {f.name for f in fields(reading)} == {"five_hour", "seven_day", "fetched_at"}


async def test_credentials_file_re_read_each_poll(tmp_path: Path):
    """Behavior 4: a token change between polls is picked up on the next poll."""
    creds = _write_credentials(tmp_path / "credentials.json", token="token-A")
    fetch = AsyncMock(return_value=_success_response())
    monitor = QuotaMonitor(
        credentials_path=creds,
        notifications=NotificationDispatcher(),
        fetch_callable=fetch,
    )
    await monitor.poll_once()
    _write_credentials(creds, token="token-B")
    await monitor.poll_once()
    first_headers = fetch.call_args_list[0].args[1]
    second_headers = fetch.call_args_list[1].args[1]
    assert first_headers["Authorization"] == "Bearer token-A"
    assert second_headers["Authorization"] == "Bearer token-B"


async def test_get_quota_returns_none_before_first_poll(tmp_path: Path):
    """Behavior 5: get_quota() returns None before any successful poll."""
    creds = _write_credentials(tmp_path / "credentials.json")
    monitor = QuotaMonitor(
        credentials_path=creds,
        notifications=NotificationDispatcher(),
        fetch_callable=AsyncMock(),
    )
    assert monitor.get_quota() is None


async def test_fetched_at_set_to_successful_poll_time(tmp_path: Path):
    """Behavior 6: fetched_at is set to the time of the successful poll."""
    creds = _write_credentials(tmp_path / "credentials.json")
    fetch = AsyncMock(return_value=_success_response())
    monitor = QuotaMonitor(
        credentials_path=creds,
        notifications=NotificationDispatcher(),
        fetch_callable=fetch,
    )
    before = datetime.now(UTC)
    await monitor.poll_once()
    after = datetime.now(UTC)
    reading = monitor.get_quota()
    assert reading is not None
    assert before <= reading.fetched_at <= after


# --- Threshold / alert logic -------------------------------------------------


async def test_crossing_80_fires_one_alert(tmp_path: Path):
    """Behavior 7: utilization crossing 80% upward fires one alert."""
    fetch = AsyncMock(
        side_effect=[
            _success_response(five_hour_util=75.0),
            _success_response(five_hour_util=82.0),
        ]
    )
    monitor, sent = _make_monitor_with_recorder(tmp_path, fetch)
    await monitor.poll_once()
    assert sent == []
    await monitor.poll_once()
    assert len(sent) == 1
    assert sent[0].data == {"window": "five_hour", "band": 80, "utilization": 82.0}


async def test_same_band_does_not_re_fire(tmp_path: Path):
    """Behavior 8: same band does not re-fire while still above threshold."""
    fetch = AsyncMock(
        side_effect=[
            _success_response(five_hour_util=82.0),
            _success_response(five_hour_util=85.0),
            _success_response(five_hour_util=88.0),
        ]
    )
    monitor, sent = _make_monitor_with_recorder(tmp_path, fetch)
    for _ in range(3):
        await monitor.poll_once()
    assert len(sent) == 1
    assert sent[0].data["band"] == 80


async def test_90_and_100_each_fire(tmp_path: Path):
    """Behavior 9: 90% and 100% upward crossings each fire their own alert."""
    fetch = AsyncMock(
        side_effect=[
            _success_response(five_hour_util=85.0),
            _success_response(five_hour_util=92.0),
            _success_response(five_hour_util=100.0),
        ]
    )
    monitor, sent = _make_monitor_with_recorder(tmp_path, fetch)
    for _ in range(3):
        await monitor.poll_once()
    assert [n.data["band"] for n in sent] == [80, 90, 100]


async def test_missed_poll_jump_fires_only_highest_band(tmp_path: Path):
    """Behavior 10: jump 60% → 95% fires only the 90 band, not 80 too."""
    fetch = AsyncMock(
        side_effect=[
            _success_response(five_hour_util=60.0),
            _success_response(five_hour_util=95.0),
        ]
    )
    monitor, sent = _make_monitor_with_recorder(tmp_path, fetch)
    await monitor.poll_once()
    await monitor.poll_once()
    assert len(sent) == 1
    assert sent[0].data["band"] == 90


async def test_windows_alerted_independently(tmp_path: Path):
    """Behavior 11: five_hour and seven_day each fire their own alerts."""
    fetch = AsyncMock(
        side_effect=[
            _success_response(five_hour_util=85.0, seven_day_util=70.0),
            _success_response(five_hour_util=85.0, seven_day_util=82.0),
        ]
    )
    monitor, sent = _make_monitor_with_recorder(tmp_path, fetch)
    await monitor.poll_once()
    await monitor.poll_once()
    events = {(n.data["window"], n.data["band"]) for n in sent}
    assert events == {("five_hour", 80), ("seven_day", 80)}


async def test_window_reset_clears_only_that_windows_fired_bands(tmp_path: Path):
    """Behavior 12: a window's resets_at advancing clears only its own bands."""
    fetch = AsyncMock(
        side_effect=[
            # Poll 1: both windows cross 80
            _success_response(
                five_hour_util=85.0,
                five_hour_resets="2026-05-20T14:00:00+00:00",
                seven_day_util=85.0,
                seven_day_resets="2026-05-22T15:00:00+00:00",
            ),
            # Poll 2: 5h resets to a new window (low util); 7d unchanged
            _success_response(
                five_hour_util=10.0,
                five_hour_resets="2026-05-20T19:00:00+00:00",
                seven_day_util=88.0,
                seven_day_resets="2026-05-22T15:00:00+00:00",
            ),
            # Poll 3: 5h crosses 80 in the NEW window; 7d crosses 90
            _success_response(
                five_hour_util=82.0,
                five_hour_resets="2026-05-20T19:00:00+00:00",
                seven_day_util=90.0,
                seven_day_resets="2026-05-22T15:00:00+00:00",
            ),
        ]
    )
    monitor, sent = _make_monitor_with_recorder(tmp_path, fetch)
    for _ in range(3):
        await monitor.poll_once()
    events = [(n.data["window"], n.data["band"]) for n in sent]
    # Poll 1: 5h-80 and 7d-80
    # Poll 2: nothing (5h reset + below; 7d unchanged + already fired 80)
    # Poll 3: 5h-80 (fired-set was cleared) and 7d-90
    assert events.count(("five_hour", 80)) == 2  # cleared and re-fired
    assert events.count(("seven_day", 80)) == 1  # NOT re-fired — 7d set preserved
    assert events.count(("seven_day", 90)) == 1


async def test_alert_text_includes_window_band_and_resets_at(tmp_path: Path):
    """Behavior 13: alert text mentions the window, the band, and resets_at."""
    fetch = AsyncMock(
        return_value=_success_response(
            five_hour_util=85.0,
            five_hour_resets="2026-05-20T14:00:00+00:00",
        )
    )
    monitor, sent = _make_monitor_with_recorder(tmp_path, fetch)
    await monitor.poll_once()
    assert len(sent) == 1
    text = sent[0].text
    assert "5-hour" in text
    assert "80" in text
    assert "2026-05-20" in text
    assert "14:00" in text


# --- Failure handling --------------------------------------------------------


async def test_transient_http_failure_does_not_crash(tmp_path: Path):
    """Behavior 14: timeout / 5xx / 401 is caught — poll_once never raises."""
    fetch = AsyncMock(side_effect=urllib.error.URLError("connection refused"))
    monitor, sent = _make_monitor_with_recorder(tmp_path, fetch)
    await monitor.poll_once()  # must not raise
    # No quota-band alerts from a failure alone (and the threshold is far away)
    band_alerts = [n for n in sent if n.kind.startswith("quota_warn")]
    assert band_alerts == []


async def test_fetched_at_not_updated_on_failed_poll(tmp_path: Path):
    """Behavior 15: fetched_at stays put when a poll fails."""
    fetch = AsyncMock(side_effect=[_success_response(), urllib.error.URLError("oops")])
    monitor, _ = _make_monitor_with_recorder(tmp_path, fetch)
    await monitor.poll_once()
    first_reading = monitor.get_quota()
    assert first_reading is not None
    first_fetched_at = first_reading.fetched_at
    await monitor.poll_once()  # fails
    second_reading = monitor.get_quota()
    assert second_reading is not None
    assert second_reading.fetched_at == first_fetched_at


async def test_missing_required_keys_treated_as_failure(tmp_path: Path):
    """Behavior 16: response without five_hour/seven_day is a parse failure."""
    fetch = AsyncMock(
        return_value={
            "five_hour": {"utilization": 50.0, "resets_at": "2026-05-20T14:00:00+00:00"},
            # no seven_day
        }
    )
    monitor, _ = _make_monitor_with_recorder(tmp_path, fetch)
    await monitor.poll_once()  # must not raise
    assert monitor.get_quota() is None  # no successful reading stored


async def test_null_resets_at_on_main_window_is_treated_as_success(tmp_path: Path):
    """Behavior: a main window with resets_at=null parses cleanly (no crash)."""
    fetch = AsyncMock(
        return_value={
            "five_hour": {"utilization": 0.0, "resets_at": None},
            "seven_day": {
                "utilization": 5.0,
                "resets_at": "2026-05-28T15:00:00+00:00",
            },
        }
    )
    monitor, sent = _make_monitor_with_recorder(tmp_path, fetch)
    await monitor.poll_once()
    reading = monitor.get_quota()
    assert reading is not None  # parse did not raise
    assert reading.five_hour.resets_at is None
    assert reading.five_hour.utilization == 0.0
    assert reading.seven_day.resets_at == datetime(2026, 5, 28, 15, 0, tzinfo=UTC)
    # No band alerts fired (utilization is far below thresholds).
    band_alerts = [n for n in sent if n.kind.startswith("quota_warn")]
    assert band_alerts == []


async def test_consecutive_failures_fire_blind_alert_once(tmp_path: Path):
    """Behavior 17a: N consecutive failures fire the meta-alert exactly once."""
    fetch = AsyncMock(side_effect=[urllib.error.URLError(f"f{i}") for i in range(6)])
    monitor, sent = _make_monitor_with_recorder(tmp_path, fetch, failure_threshold=5)
    for _ in range(6):
        await monitor.poll_once()
    blinds = [n for n in sent if n.kind == "quota_monitor_blind"]
    assert len(blinds) == 1


async def test_success_between_failures_resets_failure_counter(tmp_path: Path):
    """Behavior 17b: a single success resets the consecutive-failure count."""
    fetch = AsyncMock(
        side_effect=[
            urllib.error.URLError("1"),
            urllib.error.URLError("2"),
            urllib.error.URLError("3"),
            urllib.error.URLError("4"),
            _success_response(),  # resets the counter
            urllib.error.URLError("a"),
            urllib.error.URLError("b"),
            urllib.error.URLError("c"),
            urllib.error.URLError("d"),  # only 4 fails after the reset — below threshold
        ]
    )
    monitor, sent = _make_monitor_with_recorder(tmp_path, fetch, failure_threshold=5)
    for _ in range(9):
        await monitor.poll_once()
    blinds = [n for n in sent if n.kind == "quota_monitor_blind"]
    assert blinds == []


async def test_recovery_after_blind_fires_back_online_and_re_arms(tmp_path: Path):
    """Behavior 18: N consecutive successes after blind fire recovery; meta-alert re-arms.

    Symmetric debounce — recovery now requires the same threshold as blind so
    a single lucky poll doesn't fire a false "back online".
    """
    fetch = AsyncMock(
        side_effect=[
            urllib.error.URLError("1"),
            urllib.error.URLError("2"),
            urllib.error.URLError("3"),
            urllib.error.URLError("4"),
            urllib.error.URLError("5"),  # fires monitor_blind
            _success_response(),  # success #1 — not yet enough for recovery
            _success_response(),  # success #2
            _success_response(),  # success #3
            _success_response(),  # success #4
            _success_response(),  # success #5 → fires recovery
            urllib.error.URLError("re1"),
            urllib.error.URLError("re2"),
            urllib.error.URLError("re3"),
            urllib.error.URLError("re4"),
            urllib.error.URLError("re5"),  # fires monitor_blind AGAIN
        ]
    )
    monitor, sent = _make_monitor_with_recorder(tmp_path, fetch, failure_threshold=5)
    for _ in range(15):
        await monitor.poll_once()
    blinds = [n for n in sent if n.kind == "quota_monitor_blind"]
    recoveries = [n for n in sent if n.kind == "quota_monitor_recovered"]
    assert len(blinds) == 2
    assert len(recoveries) == 1


# --- 4-hour digest ----------------------------------------------------------


@pytest.mark.skip(reason="digest scheduling deferred to #11 — firing semantics undecided")
async def test_digest_fires_when_a_4h_slot_passes(tmp_path: Path):
    """A poll that crosses a 4-hour wall-clock anchor fires one digest."""
    clock = {"now": datetime(2026, 5, 25, 11, 58, tzinfo=UTC)}  # 2 min before 12:00 UTC

    def now() -> datetime:
        return clock["now"]

    fetch = AsyncMock(return_value=_success_response())
    monitor, sent = _make_monitor_with_recorder(tmp_path, fetch, now_callable=now)

    # First poll at 11:58 UTC — no digest yet because we haven't crossed a slot.
    await monitor.poll_once()
    assert [n for n in sent if n.kind == "quota_digest"] == []

    # Advance clock past 12:00 UTC. Next poll should fire the digest once.
    clock["now"] = datetime(2026, 5, 25, 12, 1, tzinfo=UTC)
    await monitor.poll_once()
    digests = [n for n in sent if n.kind == "quota_digest"]
    assert len(digests) == 1


@pytest.mark.skip(reason="digest scheduling deferred to #11 — firing semantics undecided")
async def test_digest_does_not_re_fire_within_the_same_slot(tmp_path: Path):
    """Multiple polls inside the same 4h slot fire the digest at most once."""
    clock = {"now": datetime(2026, 5, 25, 12, 1, tzinfo=UTC)}

    def now() -> datetime:
        return clock["now"]

    fetch = AsyncMock(return_value=_success_response())
    monitor, sent = _make_monitor_with_recorder(tmp_path, fetch, now_callable=now)

    await monitor.poll_once()  # in the 12:00 slot — fires
    clock["now"] = datetime(2026, 5, 25, 14, 30, tzinfo=UTC)  # still 12:00 slot
    await monitor.poll_once()  # no re-fire
    clock["now"] = datetime(2026, 5, 25, 15, 59, tzinfo=UTC)  # still 12:00 slot
    await monitor.poll_once()  # no re-fire

    digests = [n for n in sent if n.kind == "quota_digest"]
    assert len(digests) == 1


async def test_poll_once_never_raises_on_unexpected_exception(tmp_path: Path):
    """Behavior 19: any unexpected exception is caught — poll_once never raises."""
    fetch = AsyncMock(side_effect=RuntimeError("something weird"))
    monitor, _ = _make_monitor_with_recorder(tmp_path, fetch)
    await monitor.poll_once()  # must not raise


# --- Lifecycle ---------------------------------------------------------------


async def test_start_polls_and_stop_cancels(tmp_path: Path):
    """Behavior 20: start() begins polling; stop() cancels the task cleanly."""
    fetch = AsyncMock(return_value=_success_response())
    monitor, _ = _make_monitor_with_recorder(tmp_path, fetch, poll_seconds=0.05)
    await monitor.start()
    await asyncio.sleep(0.15)  # let the loop run a few cycles
    await monitor.stop()
    polls_during_run = fetch.call_count
    assert polls_during_run >= 1
    # After stop, no further polls happen
    await asyncio.sleep(0.1)
    assert fetch.call_count == polls_during_run
    # stop() is idempotent
    await monitor.stop()


async def test_restart_replays_alert_for_already_over_threshold_window(tmp_path: Path):
    """Behavior 21: a fresh monitor re-fires when a window is already above threshold.

    Documents the accepted in-memory-state trade-off: after a Hive restart, an
    already-over-threshold window will re-alert once. Worth one duplicate to
    avoid persistence overhead for v1.
    """
    fetch1 = AsyncMock(return_value=_success_response(five_hour_util=85.0))
    monitor1, sent1 = _make_monitor_with_recorder(tmp_path, fetch1)
    await monitor1.poll_once()
    assert len(sent1) == 1

    # Simulate restart by constructing a brand-new monitor.
    fetch2 = AsyncMock(return_value=_success_response(five_hour_util=85.0))
    monitor2, sent2 = _make_monitor_with_recorder(tmp_path, fetch2)
    await monitor2.poll_once()
    assert len(sent2) == 1
    assert sent2[0].data == {"window": "five_hour", "band": 80, "utilization": 85.0}


# --- /quota text formatter ---------------------------------------------------


def _sample_reading(*, fetched_at: datetime | None = None) -> QuotaReading:
    return QuotaReading(
        five_hour=WindowReading(
            utilization=59.0,
            resets_at=datetime(2026, 5, 20, 14, 0, tzinfo=UTC),
        ),
        seven_day=WindowReading(
            utilization=43.0,
            resets_at=datetime(2026, 5, 22, 15, 0, tzinfo=UTC),
        ),
        fetched_at=fetched_at or datetime(2026, 5, 20, 11, 0, tzinfo=UTC),
    )


def test_format_quota_shows_both_windows():
    """Behavior 22: current 5h + 7d utilization and reset times."""
    reading = _sample_reading()
    now = datetime(2026, 5, 20, 11, 1, tzinfo=UTC)
    text = format_quota_text(reading, now=now, stale_after_seconds=360.0)
    assert "5-hour" in text
    assert "59" in text
    assert "7-day" in text
    assert "43" in text
    assert "14:00" in text
    assert "15:00" in text


def test_format_quota_notes_staleness_when_reading_is_old():
    """Behavior 23: stale reading is annotated with its age."""
    reading = _sample_reading(fetched_at=datetime(2026, 5, 20, 11, 0, tzinfo=UTC))
    now = datetime(2026, 5, 20, 11, 15, tzinfo=UTC)  # 15 min later
    text = format_quota_text(reading, now=now, stale_after_seconds=360.0)  # 6 min threshold
    assert "15 min" in text.lower() or "min old" in text.lower()


def test_format_quota_handles_no_reading_yet():
    """Behavior 24: clear message when no reading exists."""
    text = format_quota_text(
        None,
        now=datetime(2026, 5, 20, 11, 0, tzinfo=UTC),
        stale_after_seconds=360.0,
    )
    assert "no reading" in text.lower() or "not yet" in text.lower()
