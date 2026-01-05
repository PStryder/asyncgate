"""Lease model - worker claim on a task."""

from datetime import datetime
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

    def is_expired(self, now: datetime | None = None) -> bool:
        """Check if lease has expired."""
        if now is None:
            now = datetime.utcnow()
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
