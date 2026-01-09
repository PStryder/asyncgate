"""AsyncGate enumerations."""

from enum import Enum


class TaskStatus(str, Enum):
    """Task lifecycle status."""

    QUEUED = "queued"
    LEASED = "leased"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELED = "canceled"

    @classmethod
    def terminal_states(cls) -> set["TaskStatus"]:
        """Return terminal states."""
        return {cls.SUCCEEDED, cls.FAILED, cls.CANCELED}

    def is_terminal(self) -> bool:
        """Check if status is terminal."""
        return self in self.terminal_states()


class Outcome(str, Enum):
    """Task outcome for terminal states."""

    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELED = "canceled"


class PrincipalKind(str, Enum):
    """Kind of principal (actor in the system)."""

    AGENT = "agent"
    WORKER = "worker"
    SERVICE = "service"
    SYSTEM = "system"
    HUMAN = "human"


class ReceiptType(str, Enum):
    """Types of receipts."""

    # Task assignment contract (Agent → AsyncGate)
    TASK_ASSIGNED = "task.assigned"
    # Task accepted by worker (Worker → AsyncGate)
    TASK_ACCEPTED = "task.accepted"
    # Task completed (Worker → AsyncGate)
    TASK_COMPLETED = "task.completed"
    # Task failed (Worker → AsyncGate)
    TASK_FAILED = "task.failed"
    # Task canceled (Agent/System -> AsyncGate)
    TASK_CANCELED = "task.canceled"
    # Retry scheduled after worker failure (non-terminal)
    TASK_RETRY_SCHEDULED = "task.retry_scheduled"
    # Result delivered to agent (AsyncGate → Agent)
    TASK_RESULT_READY = "task.result_ready"
    # Progress update (Worker → AsyncGate)
    TASK_PROGRESS = "task.progress"
    # Lease expired / requeued (AsyncGate → Agent)
    LEASE_EXPIRED = "lease.expired"
    # Receipt acknowledged (TASKER → AsyncGate)
    RECEIPT_ACKNOWLEDGED = "receipt.acknowledged"
    # System anomaly (AsyncGate → Agent)
    SYSTEM_ANOMALY = "system.anomaly"


class AnomalyKind(str, Enum):
    """Kinds of system anomalies."""

    MAX_ATTEMPTS_EXCEEDED = "max_attempts_exceeded"
    REPEATED_LEASE_EXPIRY = "repeated_lease_expiry"
    EXCESSIVE_RENEWALS = "excessive_renewals"
    STALE_SCHEDULE = "stale_schedule"
    RECEIPT_BACKLOG = "receipt_backlog"


class MisfirePolicy(str, Enum):
    """Scheduler misfire handling policy."""

    SKIP = "skip"
    CATCH_UP_ONE = "catch_up_one"
    CATCH_UP_ALL = "catch_up_all"
