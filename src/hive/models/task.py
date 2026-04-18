"""Task model — a persistent work item tracked by Hive.

Unlike Entity, Task has no state machine: `status` is a plain enum field.
Workers will later claim pending tasks via SELECT ... FOR UPDATE SKIP LOCKED
(Sprint 3), but for now the queue is user-managed via Telegram commands.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from datetime import datetime


class TaskStatus(enum.Enum):
    """Lifecycle statuses for a Hive task."""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


@dataclass
class Task:
    """A persistent work item."""

    id: int
    title: str
    status: TaskStatus
    priority: int
    created_by: str
    created_at: datetime
    description: str | None = None
    assigned_to: str | None = None
    completed_at: datetime | None = None
    retry_count: int = 0
    max_retries: int = 3
    failure_reason: str | None = None
