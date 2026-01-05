"""Audit event model - recommended for tracking."""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel


class AuditEvent(BaseModel):
    """Audit trail for task lifecycle events."""

    event_id: UUID
    tenant_id: UUID
    task_id: UUID
    event_type: str
    payload: dict[str, Any]
    created_at: datetime
