"""API request/response schemas."""

from datetime import datetime
from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, Field


# ============================================================================
# Shared schemas
# ============================================================================


class PrincipalSchema(BaseModel):
    """Principal (actor) schema."""

    kind: str = Field(..., description="Principal kind: agent, worker, service, system, human")
    id: str = Field(..., description="Principal identifier")
    instance_id: Optional[str] = Field(None, description="Optional instance/session ID")


# ============================================================================
# TASKER schemas
# ============================================================================


class BootstrapRequest(BaseModel):
    """Bootstrap request."""

    principal_kind: str
    principal_id: str
    principal_instance_id: Optional[str] = None
    since_receipt_id: Optional[UUID] = None
    max_items: Optional[int] = Field(None, ge=1, le=200)


class OpenObligationsResponse(BaseModel):
    """Open obligations response (new bootstrap model)."""

    server: dict[str, Any] = Field(..., description="Server metadata")
    relationship: dict[str, Any] = Field(..., description="Principal relationship info")
    open_obligations: list[dict[str, Any]] = Field(..., description="Uncommitted obligations from ledger")
    cursor: Optional[str] = Field(None, description="Cursor for pagination")


class CreateTaskRequest(BaseModel):
    """Create task request."""

    type: str = Field(..., description="Task type")
    payload: dict[str, Any] = Field(default_factory=dict, description="Task payload")
    requirements: Optional[dict[str, Any]] = Field(None, description="Task requirements")
    priority: Optional[int] = Field(None, description="Task priority (higher = more urgent)")
    idempotency_key: Optional[str] = Field(None, description="Idempotency key")
    max_attempts: Optional[int] = Field(None, ge=1, description="Max retry attempts")
    retry_backoff_seconds: Optional[int] = Field(None, ge=1, description="Retry backoff")
    delay_seconds: Optional[int] = Field(None, ge=0, description="Delay before eligible")


class CreateTaskResponse(BaseModel):
    """Create task response."""

    task_id: UUID
    status: str


class TaskResponse(BaseModel):
    """Task response."""

    task_id: UUID
    type: str
    payload: dict[str, Any]
    created_by: dict[str, Any]
    requirements: dict[str, Any]
    priority: int
    status: str
    attempt: int
    max_attempts: int
    created_at: datetime
    updated_at: datetime
    next_eligible_at: Optional[datetime] = None
    result: Optional[dict[str, Any]] = None
    progress: Optional[dict[str, Any]] = None


class ListTasksResponse(BaseModel):
    """List tasks response."""

    tasks: list[dict[str, Any]]
    next_cursor: Optional[str] = None


class CancelTaskRequest(BaseModel):
    """Cancel task request."""

    principal_kind: str
    principal_id: str
    reason: Optional[str] = None


class CancelTaskResponse(BaseModel):
    """Cancel task response."""

    ok: bool
    status: str


class ListReceiptsResponse(BaseModel):
    """List receipts response."""

    receipts: list[dict[str, Any]]
    next_cursor: Optional[str] = None


class AckReceiptRequest(BaseModel):
    """Acknowledge receipt request."""

    principal_kind: str
    principal_id: str


class AckReceiptResponse(BaseModel):
    """Acknowledge receipt response."""

    ok: bool


# ============================================================================
# TASKEE schemas
# ============================================================================


class LeaseClaimRequest(BaseModel):
    """Lease claim request."""

    worker_kind: str = Field(default="worker")
    worker_id: str = Field(..., description="Worker identifier")
    worker_instance_id: Optional[str] = None
    capabilities: Optional[list[str]] = Field(None, description="Worker capabilities")
    accept_types: Optional[list[str]] = Field(None, description="Task types to accept")
    max_tasks: Optional[int] = Field(1, ge=1, le=10, description="Max tasks to claim")
    lease_ttl_seconds: Optional[int] = Field(None, ge=10, le=1800, description="Lease TTL")


class LeaseInfoSchema(BaseModel):
    """Leased task info."""

    task_id: UUID
    lease_id: UUID
    type: str
    payload: dict[str, Any]
    attempt: int
    expires_at: datetime
    requirements: Optional[dict[str, Any]] = None


class LeaseClaimResponse(BaseModel):
    """Lease claim response."""

    tasks: list[LeaseInfoSchema]


class RenewLeaseRequest(BaseModel):
    """Renew lease request."""

    worker_kind: str = Field(default="worker")
    worker_id: str
    task_id: UUID
    lease_id: UUID
    extend_by_seconds: Optional[int] = Field(None, ge=10, le=1800)


class RenewLeaseResponse(BaseModel):
    """Renew lease response."""

    ok: bool
    expires_at: datetime


class ReportProgressRequest(BaseModel):
    """Report progress request."""

    worker_kind: str = Field(default="worker")
    worker_id: str
    lease_id: UUID
    progress: dict[str, Any]


class ReportProgressResponse(BaseModel):
    """Report progress response."""

    ok: bool


class CompleteTaskRequest(BaseModel):
    """Complete task request."""

    worker_kind: str = Field(default="worker")
    worker_id: str
    lease_id: UUID
    result: dict[str, Any]
    artifacts: Optional[dict[str, Any]] = None


class CompleteTaskResponse(BaseModel):
    """Complete task response."""

    ok: bool


class FailTaskRequest(BaseModel):
    """Fail task request."""

    worker_kind: str = Field(default="worker")
    worker_id: str
    lease_id: UUID
    error: dict[str, Any]
    retryable: bool = False


class FailTaskResponse(BaseModel):
    """Fail task response."""

    ok: bool
    requeued: bool
    next_eligible_at: Optional[datetime] = None


# ============================================================================
# System schemas
# ============================================================================


class HealthResponse(BaseModel):
    """Health check response."""

    status: str
    version: str


class ConfigResponse(BaseModel):
    """Config response."""

    receipt_mode: str
    memorygate_url: Optional[str] = None
    instance_id: str
    capabilities: list[str]
    version: str
