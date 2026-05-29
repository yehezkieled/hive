"""Tests for QuotaState — pure failure/success state machine with symmetric debounce."""

from __future__ import annotations

from hive.runtime.quota_state import QuotaState


def test_n_failures_in_a_row_fire_blind_once():
    """Threshold consecutive failures produce exactly one 'blind' event."""
    state = QuotaState(threshold=5)

    events = [state.record_failure() for _ in range(5)]
    assert events == ["no_event", "no_event", "no_event", "no_event", "blind"]

    extra = state.record_failure()
    assert extra == "no_event"


def test_near_miss_followed_by_success_does_not_fire_blind():
    """N-1 failures + 1 success + more failures (below threshold again) → silence."""
    state = QuotaState(threshold=5)

    for _ in range(4):
        assert state.record_failure() == "no_event"

    assert state.record_success() == "no_event"

    # 4 more failures — would have been enough cumulatively, but counter reset.
    for _ in range(4):
        assert state.record_failure() == "no_event"


def test_n_successes_after_blind_fire_recovered_once():
    """After 'blind', threshold consecutive successes produce exactly one 'recovered'."""
    state = QuotaState(threshold=5)

    # Get to blind first.
    for _ in range(4):
        state.record_failure()
    assert state.record_failure() == "blind"

    # 4 successes — still in blind territory.
    for _ in range(4):
        assert state.record_success() == "no_event"

    # 5th success → recovered.
    assert state.record_success() == "recovered"

    # Subsequent successes do not re-fire.
    assert state.record_success() == "no_event"


def test_near_miss_success_during_blind_does_not_fire_recovered():
    """N-1 successes interrupted by a failure don't fire recovered."""
    state = QuotaState(threshold=5)

    # To blind.
    for _ in range(5):
        state.record_failure()

    for _ in range(4):
        assert state.record_success() == "no_event"

    # One failure resets the success counter; still blind.
    assert state.record_failure() == "no_event"

    # 4 more successes — not enough on their own.
    for _ in range(4):
        assert state.record_success() == "no_event"


def test_state_rearms_after_recovered_for_next_blind_cycle():
    """After recovered, a fresh N consecutive failures fire blind again."""
    state = QuotaState(threshold=5)

    # Blind → recovered.
    for _ in range(5):
        state.record_failure()
    for _ in range(5):
        state.record_success()

    # New failure streak should fire blind again.
    events = [state.record_failure() for _ in range(5)]
    assert events == ["no_event", "no_event", "no_event", "no_event", "blind"]
