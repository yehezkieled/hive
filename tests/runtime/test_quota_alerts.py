"""Tests for quota_alerts — pure text formatters for QuotaMonitor notifications."""

from __future__ import annotations

from datetime import UTC, datetime

from hive.runtime.quota_alerts import (
    format_band_alert,
    format_digest,
    format_recovery_alert,
    format_unreachable_alert,
)
from hive.runtime.quota_monitor import QuotaReading, WindowReading


def _reading(
    *,
    five_util: float = 6.0,
    five_resets: datetime | None = datetime(2026, 5, 25, 0, 0, tzinfo=UTC),
    seven_util: float = 5.0,
    seven_resets: datetime | None = datetime(2026, 5, 28, 15, 0, tzinfo=UTC),
    fetched_at: datetime = datetime(2026, 5, 24, 22, 40, tzinfo=UTC),
) -> QuotaReading:
    return QuotaReading(
        five_hour=WindowReading(utilization=five_util, resets_at=five_resets),
        seven_day=WindowReading(utilization=seven_util, resets_at=seven_resets),
        fetched_at=fetched_at,
    )


def test_recovery_alert_includes_current_utilization_and_reset_times():
    """Behavior: 'back online' message carries the fresh reading."""
    text = format_recovery_alert(_reading())
    assert "back online" in text.lower()
    assert "5-hour" in text
    assert "6%" in text
    assert "7-day" in text
    assert "5%" in text
    assert "2026-05-25 00:00 UTC" in text
    assert "2026-05-28 15:00 UTC" in text


def test_band_alert_names_window_band_and_reset():
    """Behavior: 80/90/100 band alerts include window, band crossed, reset."""
    window = WindowReading(
        utilization=82.0,
        resets_at=datetime(2026, 5, 25, 0, 0, tzinfo=UTC),
    )
    text = format_band_alert("five_hour", 80, window)
    assert "5-hour" in text
    assert "80" in text
    assert "2026-05-25 00:00 UTC" in text


def test_band_alert_100_uses_exhausted_wording():
    """Behavior: 100% reads as exhausted, not just 'crossed 100'."""
    window = WindowReading(
        utilization=100.0,
        resets_at=datetime(2026, 5, 25, 0, 0, tzinfo=UTC),
    )
    text = format_band_alert("seven_day", 100, window)
    assert "exhausted" in text.lower()


def test_unreachable_alert_includes_last_known_reading_and_age():
    """Behavior: 'endpoint unreachable' carries the last reading + its age."""
    reading = _reading(
        five_util=12.0,
        seven_util=5.0,
        fetched_at=datetime(2026, 5, 24, 22, 32, tzinfo=UTC),
    )
    now = datetime(2026, 5, 24, 22, 40, tzinfo=UTC)  # 8 min later
    text = format_unreachable_alert(reading, now=now)
    assert "unreachable" in text.lower()
    assert "12%" in text
    assert "5%" in text
    assert "8 min" in text


def test_unreachable_alert_without_prior_reading_omits_data_line():
    """Behavior: with no prior reading ever, the data line is left off entirely."""
    text = format_unreachable_alert(None, now=datetime(2026, 5, 24, 22, 40, tzinfo=UTC))
    assert "unreachable" in text.lower()
    # No percentage signs since there's nothing to report.
    assert "%" not in text
    # Should not crash, should not say "0%" or "None".
    assert "None" not in text


def test_digest_includes_current_5h_and_7d_with_resets():
    """Behavior: digest carries the current snapshot."""
    reading = _reading(fetched_at=datetime(2026, 5, 25, 12, 0, tzinfo=UTC))
    now = datetime(2026, 5, 25, 12, 0, tzinfo=UTC)  # same moment
    text = format_digest(reading, now=now, stale_after_seconds=360.0)
    assert "digest" in text.lower()
    assert "5-hour" in text
    assert "6%" in text
    assert "7-day" in text
    assert "5%" in text
    # Fresh reading — no staleness note.
    assert "min old" not in text


def test_digest_with_stale_reading_includes_staleness_note():
    """Behavior: digest fired while monitor is blind annotates the reading's age."""
    reading = _reading(fetched_at=datetime(2026, 5, 25, 11, 42, tzinfo=UTC))
    now = datetime(2026, 5, 25, 12, 0, tzinfo=UTC)  # 18 min later
    text = format_digest(reading, now=now, stale_after_seconds=360.0)  # 6 min threshold
    assert "min old" in text.lower() or "stale" in text.lower()
    assert "18" in text


def test_window_with_null_resets_at_renders_as_not_started():
    """Behavior: null resets_at appears as 'not started' (no fabricated date)."""
    reading = _reading(five_resets=None)
    text = format_recovery_alert(reading)
    assert "not started" in text.lower()
    # The other window still shows its real reset time.
    assert "2026-05-28 15:00 UTC" in text
