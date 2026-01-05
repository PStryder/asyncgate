"""Relationship model - tracks agent/worker sessions."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel

from asyncgate.models.enums import PrincipalKind


class Relationship(BaseModel):
    """Tracks relationship between AsyncGate and a principal."""

    tenant_id: UUID
    principal_kind: PrincipalKind
    principal_id: str
    principal_instance_id: str | None = None
    first_seen_at: datetime
    last_seen_at: datetime
    sessions_count: int = 1
