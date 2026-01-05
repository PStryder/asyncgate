"""Progress model - optional task progress tracking."""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel


class Progress(BaseModel):
    """Tracks progress of a task during execution."""

    task_id: UUID
    tenant_id: UUID
    progress: dict[str, Any]
    updated_at: datetime
