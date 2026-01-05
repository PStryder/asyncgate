"""Receipt model - immutable contract records."""

from datetime import datetime
from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, Field

from asyncgate.models.enums import ReceiptType
from asyncgate.models.principal import Principal


class Receipt(BaseModel):
    """Immutable record of obligations and state changes."""

    receipt_id: UUID
    tenant_id: UUID
    receipt_type: ReceiptType
    created_at: datetime

    # Sender and recipient (using trailing underscore to avoid reserved keywords)
    from_: Principal = Field(serialization_alias="from", validation_alias="from")
    to_: Principal = Field(serialization_alias="to", validation_alias="to")

    # Related entities (nullable)
    task_id: Optional[UUID] = None
    lease_id: Optional[UUID] = None
    schedule_id: Optional[str] = None

    # Causal linkage
    parents: list[UUID] = Field(default_factory=list)

    # Type-specific payload
    body: dict[str, Any] = Field(default_factory=dict)

    # Integrity (for deduplication/equivalence, not crypto verification)
    hash: Optional[str] = None

    # AsyncGate instance that owns this receipt's task
    asyncgate_instance: Optional[str] = None

    # Delivery tracking
    delivered_at: Optional[datetime] = None

    model_config = {"populate_by_name": True}


class ReceiptBody:
    """Standard receipt body schemas."""

    @staticmethod
    def task_assigned(
        instructions: str,
        requirements: dict | None = None,
        success_criteria: str | None = None,
        result_delivery: str = "return_payload",
        timeouts: dict | None = None,
    ) -> dict[str, Any]:
        """Body for task.assigned receipt."""
        return {
            "instructions": instructions,
            "requirements": requirements or {},
            "success_criteria": success_criteria,
            "result_delivery": result_delivery,
            "timeouts": timeouts or {},
        }

    @staticmethod
    def task_accepted(
        worker_capabilities: list[str],
        expected_duration: int | None = None,
    ) -> dict[str, Any]:
        """Body for task.accepted receipt."""
        return {
            "worker_capabilities": worker_capabilities,
            "expected_duration": expected_duration,
        }

    @staticmethod
    def task_completed(
        result_summary: str,
        result_payload: dict | None = None,
        artifacts: dict | None = None,
        completion_metadata: dict | None = None,
    ) -> dict[str, Any]:
        """Body for task.completed receipt."""
        return {
            "result_summary": result_summary,
            "result_payload": result_payload,
            "artifacts": artifacts,
            "completion_metadata": completion_metadata or {},
        }

    @staticmethod
    def task_failed(
        error: dict[str, Any],
        retry_recommended: bool = False,
        retry_after_seconds: int | None = None,
    ) -> dict[str, Any]:
        """Body for task.failed receipt."""
        return {
            "error": error,
            "retry_recommended": retry_recommended,
            "retry_after_seconds": retry_after_seconds,
        }

    @staticmethod
    def task_result_ready(
        status: str,
        result_payload: dict | None = None,
        error: dict | None = None,
        artifacts: dict | None = None,
        how_to_retrieve: str | None = None,
    ) -> dict[str, Any]:
        """Body for task.result_ready receipt."""
        return {
            "status": status,
            "result_payload": result_payload,
            "error": error,
            "artifacts": artifacts,
            "how_to_retrieve": how_to_retrieve,
        }

    @staticmethod
    def lease_expired(
        task_id: UUID,
        previous_worker_id: str,
        attempt: int,
        requeued: bool = True,
    ) -> dict[str, Any]:
        """Body for lease.expired receipt."""
        return {
            "task_id": str(task_id),
            "previous_worker_id": previous_worker_id,
            "attempt": attempt,
            "requeued": requeued,
        }

    @staticmethod
    def system_anomaly(
        kind: str,
        details: dict[str, Any],
        recommended_action: str | None = None,
    ) -> dict[str, Any]:
        """Body for system.anomaly receipt."""
        return {
            "kind": kind,
            "details": details,
            "recommended_action": recommended_action,
        }
