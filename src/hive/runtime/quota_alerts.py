"""Pure formatters for QuotaMonitor notifications.

One small module that owns every alert text shape — band alerts, blind /
recovered meta-alerts, the 4-hour digest, and the on-demand `/quota` response.
All functions are pure: caller supplies the data, the formatter returns text.
"""

from __future__ import annotations

from datetime import UTC, datetime

from hive.runtime.quota_monitor import QuotaReading, WindowReading

_BAND_LABEL: dict[int, str] = {
    80: "Crossed 80%",
    90: "Crossed 90%",
    100: "EXHAUSTED (100%)",
}
_WINDOW_LABEL: dict[str, str] = {
    "five_hour": "5-hour window",
    "seven_day": "7-day window",
}


def _format_window_line(label: str, window: WindowReading) -> str:
    reset_str = (
        window.resets_at.astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC")
        if window.resets_at is not None
        else "not started"
    )
    return f"{label}: {window.utilization:.0f}%, resets {reset_str}"


def format_band_alert(window_name: str, band: int, window: WindowReading) -> str:
    window_label = _WINDOW_LABEL[window_name]
    band_label = _BAND_LABEL[band]
    reset_str = (
        window.resets_at.astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC")
        if window.resets_at is not None
        else "not started"
    )
    return f"Hive quota — {window_label}\n{band_label}. Resets at {reset_str}."


def format_recovery_alert(reading: QuotaReading) -> str:
    return (
        "QuotaMonitor — back online\n"
        f"{_format_window_line('5-hour', reading.five_hour)}\n"
        f"{_format_window_line('7-day', reading.seven_day)}"
    )


def format_unreachable_alert(
    last_reading: QuotaReading | None,
    *,
    now: datetime,
) -> str:
    header = "QuotaMonitor — endpoint unreachable"
    closing = "Quota alerts are offline until it recovers."
    if last_reading is None:
        return f"{header}\n{closing}"
    age_min = int((now - last_reading.fetched_at).total_seconds() / 60)
    data_line = (
        f"Last reading {age_min} min ago: "
        f"5h {last_reading.five_hour.utilization:.0f}%, "
        f"7d {last_reading.seven_day.utilization:.0f}%."
    )
    return f"{header}\n{data_line}\n{closing}"


def format_digest(reading: QuotaReading, *, now: datetime, stale_after_seconds: float) -> str:
    header = "Hive quota — digest"
    age_seconds = (now - reading.fetched_at).total_seconds()
    if age_seconds > stale_after_seconds:
        minutes = int(age_seconds / 60)
        header += f" (reading {minutes} min old — endpoint may be down)"
    return (
        f"{header}\n"
        f"{_format_window_line('5-hour', reading.five_hour)}\n"
        f"{_format_window_line('7-day', reading.seven_day)}"
    )
