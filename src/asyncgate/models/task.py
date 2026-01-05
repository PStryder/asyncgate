"""Task model - core work unit."""

from datetime import datetime
from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, Field

from asyncgate.models.enums import Outcome, TaskStatus
from asyncgate.models.principal import Principal


class TaskRequirements(BaseModel):
    """Requirements for task execution."""

    capabilities: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)


class TaskResult(BaseModel):
    """Terminal outcome of a task."""

    outcome: Outcome
    result: Optional[dict[str, Any]] = None
    error: Optional[dict[str, Any]] = None
    artifacts: Optional[dict[str, Any]] = None
    completed_at: datetime


class Task(BaseModel):
    """Core task entity representing a unit of work."""

    # Identity
    task_id: UUID
    tenant_id: UUID

    # Type and payload
    type: str
    payload: dict[str, Any] = Field(default_factory=dict)

    # Ownership (immutable after creation)
    created_by: Principal

    # Requirements for execution
    requirements: TaskRequirements = Field(default_factory=TaskRequirements)

    # Priority (higher = more urgent)
    priority: int = 0

    # Status
    status: TaskStatus = TaskStatus.QUEUED

    # Retry configuration
    attempt: int = 0
    max_attempts: int = 3
    retry_backoff_seconds: int = 30

    # Idempotency
    idempotency_key: Optional[str] = None

    # Timestamps
    created_at: datetime
    updated_at: datetime
    next_eligible_at: Optional[datetime] = None

    # Result (populated when terminal)
    result: Optional[TaskResult] = None

    # AsyncGate instance ownership (for multi-instance deployments)
    asyncgate_instance: Optional[str] = None

    def is_terminal(self) -> bool:
        """Check if task is in a terminal state."""
        return self.status.is_terminal()

    def can_transition_to(self, new_status: TaskStatus) -> bool:
        """Check if transition to new status is valid per state machine."""
        valid_transitions: dict[TaskStatus, set[TaskStatus]] = {
            TaskStatus.QUEUED: {TaskStatus.LEASED, TaskStatus.CANCELED},
            TaskStatus.LEASED: {
                TaskStatus.SUCCEEDED,
                TaskStatus.FAILED,
                TaskStatus.CANCELED,
                TaskStatus.QUEUED,  # On lease expiry (system-driven)
            },
            TaskStatus.FAILED: {TaskStatus.QUEUED},  # Only if retry requeues
            TaskStatus.SUCCEEDED: set(),
            TaskStatus.CANCELED: set(),
        }
        return new_status in valid_transitions.get(self.status, set())


class TaskSummary(BaseModel):
    """Lightweight task summary for bootstrap responses."""

    task_id: UUID
    type: str
    status: TaskStatus
    priority: int
    attempt: int
    created_at: datetime
    updated_at: datetime
    next_eligible_at: Optional[datetime] = None
