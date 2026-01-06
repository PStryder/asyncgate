"""Lease model - worker claim on a task."""

from datetime import datetime, timezone
from uuid import UUID

from pydantic import BaseModel


class Lease(BaseModel):
    """Represents a worker's exclusive claim on a task."""

    lease_id: UUID
    tenant_id: UUID
    task_id: UUID
    worker_id: str
    expires_at: datetime
    created_at: datetime
    acquired_at: datetime  # P1.1: When lease was initially acquired
    renewal_count: int = 0  # P1.1: Number of renewals

    def is_expired(self, now: datetime | None = None) -> bool:
        """Check if lease has expired."""
        if now is None:
            now = datetime.now(timezone.utc)
        return now >= self.expires_at


class LeaseInfo(BaseModel):
    """Lease information returned to workers."""

    task_id: UUID
    lease_id: UUID
    type: str
    payload: dict
    attempt: int
    expires_at: datetime
    requirements: dict | None = None
