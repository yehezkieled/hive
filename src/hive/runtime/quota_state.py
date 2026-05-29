"""QuotaState — pure state machine for QuotaMonitor's blind/recovered transitions.

Tracks consecutive failures and successes; emits a transition event only when
the symmetric threshold is crossed. Pure (no I/O, no clock reads) — caller
feeds events, state returns the transition that fired this tick (or
``"no_event"``).
"""

from __future__ import annotations

from typing import Literal

TransitionEvent = Literal["no_event", "blind", "recovered"]


class QuotaState:
    """Symmetric-debounce state machine for the QuotaMonitor meta-alerts."""

    def __init__(self, threshold: int = 5) -> None:
        self._threshold = threshold
        self._failures = 0
        self._successes = 0
        self._blind = False

    def record_failure(self) -> TransitionEvent:
        self._successes = 0
        self._failures += 1
        if self._failures == self._threshold and not self._blind:
            self._blind = True
            return "blind"
        return "no_event"

    def record_success(self) -> TransitionEvent:
        self._failures = 0
        self._successes += 1
        if self._successes == self._threshold and self._blind:
            self._blind = False
            return "recovered"
        return "no_event"
