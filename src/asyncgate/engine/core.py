"""AsyncGate core engine - canonical operations."""

import hashlib
import json
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from asyncgate.config import settings
from asyncgate.db.repositories import (
    LeaseRepository,
    ProgressRepository,
    ReceiptRepository,
    RelationshipRepository,
    TaskRepository,
)
from asyncgate.engine.errors import (
    InvalidStateTransition,
    LeaseInvalidOrExpired,
    TaskNotFound,
    UnauthorizedError,
)
from asyncgate.models import (
    Lease,
    Principal,
    PrincipalKind,
    Progress,
    Receipt,
    ReceiptType,
    Relationship,
    Task,
    TaskRequirements,
    TaskResult,
    TaskStatus,
    TaskSummary,
)
from asyncgate.models.enums import AnomalyKind, Outcome
from asyncgate.models.lease import LeaseInfo
from asyncgate.models.receipt import ReceiptBody


class AsyncGateEngine:
    """Core engine implementing canonical AsyncGate operations."""

    def __init__(self, session: AsyncSession):
        self.session = session
        self.tasks = TaskRepository(session)
        self.leases = LeaseRepository(session)
        self.receipts = ReceiptRepository(session)
        self.progress = ProgressRepository(session)
        self.relationships = RelationshipRepository(session)

    # =========================================================================
    # TASKER Operations (post/observe/control)
    # =========================================================================

    async def bootstrap(
        self,
        tenant_id: UUID,
        principal: Principal,
        since_receipt_id: UUID | None = None,
        max_items: int | None = None,
    ) -> dict[str, Any]:
        """
        Establish session identity and return attention-aware status packet.

        Bootstrap is idempotent and safe to call frequently.
        """
        max_items = min(
            max_items or settings.default_bootstrap_max_items,
            settings.max_bootstrap_max_items,
        )

        # Update relationship
        relationship = await self.relationships.upsert(
            tenant_id=tenant_id,
            principal_kind=principal.kind,
            principal_id=principal.id,
            principal_instance_id=principal.instance_id,
        )

        # Get inbox receipts
        inbox_receipts, next_cursor = await self.receipts.list(
            tenant_id=tenant_id,
            to_kind=principal.kind,
            to_id=principal.id,
            since_receipt_id=since_receipt_id,
            limit=max_items,
        )

        # Mark receipts as delivered
        if inbox_receipts:
            receipt_ids = [r.receipt_id for r in inbox_receipts]
            await self.receipts.mark_delivered(tenant_id, receipt_ids)

        # Get assigned tasks (owned by this principal)
        assigned_tasks, _ = await self.tasks.list(
            tenant_id=tenant_id,
            created_by_id=principal.id,
            limit=max_items,
        )

        # Filter for attention categories
        waiting_results = [
            self._task_to_summary(t)
            for t in assigned_tasks
            if t.is_terminal()
        ]
        running_or_scheduled = [
            self._task_to_summary(t)
            for t in assigned_tasks
            if not t.is_terminal()
        ]

        # Build anomalies (from receipts or detected conditions)
        anomalies = [
            r.body for r in inbox_receipts
            if r.receipt_type == ReceiptType.SYSTEM_ANOMALY
        ]

        return {
            "server": {
                "name": "AsyncGate",
                "version": "0.1.0",
                "instance_id": settings.instance_id,
                "uptime": 0,  # TODO: track server uptime
                "environment": settings.env.value,
            },
            "relationship": {
                "principal_kind": relationship.principal_kind.value,
                "principal_id": relationship.principal_id,
                "principal_instance_id": relationship.principal_instance_id,
                "first_seen_at": relationship.first_seen_at.isoformat(),
                "last_seen_at": relationship.last_seen_at.isoformat(),
                "sessions_count": relationship.sessions_count,
            },
            "attention": {
                "inbox_receipts": [self._receipt_to_dict(r) for r in inbox_receipts],
                "assigned_tasks": [s.model_dump() for s in running_or_scheduled],
                "waiting_results": [s.model_dump() for s in waiting_results],
                "running_or_scheduled": [s.model_dump() for s in running_or_scheduled],
                "anomalies": anomalies,
            },
            "cursor": {
                "latest_receipt_id": str(next_cursor) if next_cursor else None,
            },
        }

    async def create_task(
        self,
        tenant_id: UUID,
        type: str,
        payload: dict[str, Any],
        created_by: Principal,
        requirements: dict[str, Any] | None = None,
        priority: int | None = None,
        idempotency_key: str | None = None,
        max_attempts: int | None = None,
        retry_backoff_seconds: int | None = None,
        delay_seconds: int | None = None,
    ) -> dict[str, Any]:
        """
        Create a new task.

        If idempotency_key is provided and matches existing task, returns that task.
        """
        task_requirements = None
        if requirements:
            task_requirements = TaskRequirements(**requirements)

        task = await self.tasks.create(
            tenant_id=tenant_id,
            type=type,
            payload=payload,
            created_by=created_by,
            requirements=task_requirements,
            priority=priority or settings.default_priority,
            idempotency_key=idempotency_key,
            max_attempts=max_attempts,
            retry_backoff_seconds=retry_backoff_seconds,
            delay_seconds=delay_seconds,
        )

        # Emit task.assigned receipt
        asyncgate_principal = Principal(kind=PrincipalKind.SYSTEM, id="asyncgate")
        await self._emit_receipt(
            tenant_id=tenant_id,
            receipt_type=ReceiptType.TASK_ASSIGNED,
            from_principal=created_by,
            to_principal=asyncgate_principal,
            task_id=task.task_id,
            body=ReceiptBody.task_assigned(
                instructions=f"Execute task type: {type}",
                requirements=task.requirements.model_dump() if task.requirements else None,
            ),
        )

        return {"task_id": str(task.task_id), "status": task.status.value}

    async def get_task(
        self,
        tenant_id: UUID,
        task_id: UUID,
    ) -> dict[str, Any]:
        """Get a task by ID, including result if terminal."""
        task = await self.tasks.get(tenant_id, task_id)
        if not task:
            raise TaskNotFound(str(task_id))

        result = self._task_to_dict(task)

        # Include progress if available
        progress = await self.progress.get(tenant_id, task_id)
        if progress:
            result["progress"] = progress.progress

        return result

    async def list_tasks(
        self,
        tenant_id: UUID,
        status: str | None = None,
        type: str | None = None,
        created_by_id: str | None = None,
        limit: int | None = None,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        """List tasks with optional filtering."""
        task_status = TaskStatus(status) if status else None
        limit = min(limit or settings.default_list_limit, settings.max_list_limit)

        tasks, next_cursor = await self.tasks.list(
            tenant_id=tenant_id,
            status=task_status,
            type=type,
            created_by_id=created_by_id,
            limit=limit,
            cursor=cursor,
        )

        return {
            "tasks": [self._task_to_dict(t) for t in tasks],
            "next_cursor": next_cursor,
        }

    async def cancel_task(
        self,
        tenant_id: UUID,
        task_id: UUID,
        principal: Principal,
        reason: str | None = None,
    ) -> dict[str, Any]:
        """Cancel a task."""
        task = await self.tasks.get(tenant_id, task_id)
        if not task:
            raise TaskNotFound(str(task_id))

        # Authorization: only task owner or system can cancel
        if principal.kind != PrincipalKind.SYSTEM:
            if task.created_by.id != principal.id or task.created_by.kind != principal.kind:
                raise UnauthorizedError(
                    f"Principal {principal.kind.value}:{principal.id} "
                    f"not authorized to cancel task owned by "
                    f"{task.created_by.kind.value}:{task.created_by.id}"
                )

        if task.is_terminal():
            return {"ok": False, "status": task.status.value}

        # Release any active lease
        await self.leases.release(tenant_id, task_id)

        # Cancel the task
        task = await self.tasks.cancel(tenant_id, task_id, reason)

        # Emit result_ready receipt to owner
        await self._emit_result_ready_receipt(tenant_id, task)

        return {"ok": True, "status": task.status.value}

    async def list_receipts(
        self,
        tenant_id: UUID,
        to_kind: str,
        to_id: str,
        since_receipt_id: UUID | None = None,
        limit: int | None = None,
    ) -> dict[str, Any]:
        """List receipts for a principal."""
        limit = min(limit or settings.default_list_limit, settings.max_list_limit)

        receipts, next_cursor = await self.receipts.list(
            tenant_id=tenant_id,
            to_kind=PrincipalKind(to_kind),
            to_id=to_id,
            since_receipt_id=since_receipt_id,
            limit=limit,
        )

        return {
            "receipts": [self._receipt_to_dict(r) for r in receipts],
            "next_cursor": str(next_cursor) if next_cursor else None,
        }

    async def ack_receipt(
        self,
        tenant_id: UUID,
        receipt_id: UUID,
        principal: Principal,
    ) -> dict[str, Any]:
        """
        Acknowledge a receipt.

        Implemented as append-only receipt.acknowledged receipt, not a mutable flag.
        """
        asyncgate_principal = Principal(kind=PrincipalKind.SYSTEM, id="asyncgate")

        await self._emit_receipt(
            tenant_id=tenant_id,
            receipt_type=ReceiptType.RECEIPT_ACKNOWLEDGED,
            from_principal=principal,
            to_principal=asyncgate_principal,
            body={"acknowledged_receipt_id": str(receipt_id)},
            parents=[receipt_id],
        )

        return {"ok": True}

    # =========================================================================
    # TASKEE Operations (lease/execute/report)
    # =========================================================================

    async def lease_next(
        self,
        tenant_id: UUID,
        worker_id: str,
        capabilities: list[str] | None = None,
        accept_types: list[str] | None = None,
        max_tasks: int = 1,
        lease_ttl_seconds: int | None = None,
    ) -> dict[str, Any]:
        """
        Claim next available tasks matching capabilities.

        Selection rules:
        - Only tasks with status=queued
        - Only tasks with next_eligible_at <= now (or null)
        - Requirements/capabilities must match if specified
        - Highest priority first, then FIFO by created_at
        """
        max_tasks = min(max_tasks, 10)  # Cap at 10 per request

        leases = await self.leases.claim_next(
            tenant_id=tenant_id,
            worker_id=worker_id,
            capabilities=capabilities,
            accept_types=accept_types,
            max_tasks=max_tasks,
            lease_ttl_seconds=lease_ttl_seconds,
        )

        # Build response with task details
        results = []
        for lease in leases:
            task = await self.tasks.get(tenant_id, lease.task_id)
            if task:
                # Emit task.accepted receipt
                worker_principal = Principal(kind=PrincipalKind.WORKER, id=worker_id)
                asyncgate_principal = Principal(kind=PrincipalKind.SYSTEM, id="asyncgate")

                await self._emit_receipt(
                    tenant_id=tenant_id,
                    receipt_type=ReceiptType.TASK_ACCEPTED,
                    from_principal=worker_principal,
                    to_principal=asyncgate_principal,
                    task_id=task.task_id,
                    lease_id=lease.lease_id,
                    body=ReceiptBody.task_accepted(
                        worker_capabilities=capabilities or [],
                    ),
                )

                results.append({
                    "task_id": str(task.task_id),
                    "lease_id": str(lease.lease_id),
                    "type": task.type,
                    "payload": task.payload,
                    "attempt": task.attempt,
                    "expires_at": lease.expires_at.isoformat(),
                    "requirements": task.requirements.model_dump() if task.requirements else None,
                })

        return {"tasks": results}

    async def renew_lease(
        self,
        tenant_id: UUID,
        worker_id: str,
        task_id: UUID,
        lease_id: UUID,
        extend_by_seconds: int | None = None,
    ) -> dict[str, Any]:
        """Renew an active lease."""
        # Validate task is in correct state
        task = await self.tasks.get(tenant_id, task_id)
        if not task:
            raise TaskNotFound(str(task_id))

        if task.status not in (TaskStatus.LEASED, TaskStatus.RUNNING):
            raise LeaseInvalidOrExpired(str(task_id), str(lease_id))

        lease = await self.leases.renew(
            tenant_id=tenant_id,
            task_id=task_id,
            lease_id=lease_id,
            worker_id=worker_id,
            extend_by_seconds=extend_by_seconds,
        )

        if not lease:
            raise LeaseInvalidOrExpired(str(task_id), str(lease_id))

        return {"ok": True, "expires_at": lease.expires_at.isoformat()}

    async def report_progress(
        self,
        tenant_id: UUID,
        worker_id: str,
        task_id: UUID,
        lease_id: UUID,
        progress_data: dict[str, Any],
    ) -> dict[str, Any]:
        """Report task execution progress."""
        # Validate lease
        lease = await self.leases.validate(tenant_id, task_id, lease_id, worker_id)
        if not lease:
            raise LeaseInvalidOrExpired(str(task_id), str(lease_id))

        # Update progress
        await self.progress.update(tenant_id, task_id, progress_data)

        # Emit progress receipt
        worker_principal = Principal(kind=PrincipalKind.WORKER, id=worker_id)
        asyncgate_principal = Principal(kind=PrincipalKind.SYSTEM, id="asyncgate")

        await self._emit_receipt(
            tenant_id=tenant_id,
            receipt_type=ReceiptType.TASK_PROGRESS,
            from_principal=worker_principal,
            to_principal=asyncgate_principal,
            task_id=task_id,
            lease_id=lease_id,
            body={"progress": progress_data},
        )

        return {"ok": True}

    async def complete(
        self,
        tenant_id: UUID,
        worker_id: str,
        task_id: UUID,
        lease_id: UUID,
        result: dict[str, Any],
        artifacts: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Mark a task as successfully completed."""
        # Validate lease
        lease = await self.leases.validate(tenant_id, task_id, lease_id, worker_id)
        if not lease:
            raise LeaseInvalidOrExpired(str(task_id), str(lease_id))

        task = await self.tasks.get(tenant_id, task_id)
        if not task:
            raise TaskNotFound(str(task_id))

        if not task.can_transition_to(TaskStatus.SUCCEEDED):
            raise InvalidStateTransition(task.status.value, TaskStatus.SUCCEEDED.value)

        # Update task to succeeded
        task_result = TaskResult(
            outcome=Outcome.SUCCEEDED,
            result=result,
            artifacts=artifacts,
            completed_at=datetime.utcnow(),
        )
        await self.tasks.update_status(tenant_id, task_id, TaskStatus.SUCCEEDED, task_result)

        # Release lease
        await self.leases.release(tenant_id, task_id)

        # Emit task.completed receipt
        worker_principal = Principal(kind=PrincipalKind.WORKER, id=worker_id)
        asyncgate_principal = Principal(kind=PrincipalKind.SYSTEM, id="asyncgate")

        await self._emit_receipt(
            tenant_id=tenant_id,
            receipt_type=ReceiptType.TASK_COMPLETED,
            from_principal=worker_principal,
            to_principal=asyncgate_principal,
            task_id=task_id,
            lease_id=lease_id,
            body=ReceiptBody.task_completed(
                result_summary="Task completed successfully",
                result_payload=result,
                artifacts=artifacts,
            ),
        )

        # Emit result_ready to task owner
        task = await self.tasks.get(tenant_id, task_id)
        await self._emit_result_ready_receipt(tenant_id, task)

        return {"ok": True}

    async def fail(
        self,
        tenant_id: UUID,
        worker_id: str,
        task_id: UUID,
        lease_id: UUID,
        error: dict[str, Any],
        retryable: bool = False,
    ) -> dict[str, Any]:
        """
        Mark a task as failed.

        If retryable and attempts remaining, requeue with backoff.
        Otherwise mark as terminal failure.
        """
        # Validate lease
        lease = await self.leases.validate(tenant_id, task_id, lease_id, worker_id)
        if not lease:
            raise LeaseInvalidOrExpired(str(task_id), str(lease_id))

        task = await self.tasks.get(tenant_id, task_id)
        if not task:
            raise TaskNotFound(str(task_id))

        # Release lease
        await self.leases.release(tenant_id, task_id)

        # Check if should retry
        should_requeue = retryable and (task.attempt + 1) < task.max_attempts
        next_eligible_at = None

        if should_requeue:
            task = await self.tasks.requeue_with_backoff(tenant_id, task_id, increment_attempt=True)
            next_eligible_at = task.next_eligible_at
        else:
            # Terminal failure
            task_result = TaskResult(
                outcome=Outcome.FAILED,
                error=error,
                completed_at=datetime.utcnow(),
            )
            await self.tasks.update_status(tenant_id, task_id, TaskStatus.FAILED, task_result)

            # Emit result_ready to task owner
            task = await self.tasks.get(tenant_id, task_id)
            await self._emit_result_ready_receipt(tenant_id, task)

        # Emit task.failed receipt
        worker_principal = Principal(kind=PrincipalKind.WORKER, id=worker_id)
        asyncgate_principal = Principal(kind=PrincipalKind.SYSTEM, id="asyncgate")

        await self._emit_receipt(
            tenant_id=tenant_id,
            receipt_type=ReceiptType.TASK_FAILED,
            from_principal=worker_principal,
            to_principal=asyncgate_principal,
            task_id=task_id,
            lease_id=lease_id,
            body=ReceiptBody.task_failed(
                error=error,
                retry_recommended=retryable,
            ),
        )

        return {
            "ok": True,
            "requeued": should_requeue,
            "next_eligible_at": next_eligible_at.isoformat() if next_eligible_at else None,
        }

    # =========================================================================
    # System Operations
    # =========================================================================

    async def expire_leases(self) -> int:
        """
        Expire stale leases and requeue tasks.

        Called by background sweep task.
        """
        expired_leases = await self.leases.get_expired(limit=100)
        count = 0

        for lease in expired_leases:
            task = await self.tasks.get(lease.tenant_id, lease.task_id)
            if not task or task.is_terminal():
                continue

            # Requeue task
            await self.tasks.requeue_with_backoff(
                lease.tenant_id,
                lease.task_id,
                increment_attempt=True,
            )

            # Release expired lease
            await self.leases.release(lease.tenant_id, lease.task_id)

            # Emit lease.expired receipt to task owner
            asyncgate_principal = Principal(kind=PrincipalKind.SYSTEM, id="asyncgate")

            await self._emit_receipt(
                tenant_id=lease.tenant_id,
                receipt_type=ReceiptType.LEASE_EXPIRED,
                from_principal=asyncgate_principal,
                to_principal=task.created_by,
                task_id=task.task_id,
                lease_id=lease.lease_id,
                body=ReceiptBody.lease_expired(
                    task_id=task.task_id,
                    previous_worker_id=lease.worker_id,
                    attempt=task.attempt,
                    requeued=True,
                ),
            )

            count += 1

        return count

    async def get_config(self) -> dict[str, Any]:
        """Return operational configuration."""
        return {
            "receipt_mode": settings.receipt_mode.value,
            "memorygate_url": settings.memorygate_url,
            "instance_id": settings.instance_id,
            "capabilities": ["lease_based_execution", "receipt_emission"],
            "version": "0.1.0",
        }

    # =========================================================================
    # Internal Helpers
    # =========================================================================

    async def _emit_receipt(
        self,
        tenant_id: UUID,
        receipt_type: ReceiptType,
        from_principal: Principal,
        to_principal: Principal,
        task_id: UUID | None = None,
        lease_id: UUID | None = None,
        schedule_id: str | None = None,
        body: dict[str, Any] | None = None,
        parents: list[UUID] | None = None,
    ) -> Receipt:
        """Emit a receipt (either locally or to MemoryGate)."""
        # Compute hash for idempotency
        receipt_hash = self._compute_receipt_hash(
            receipt_type, task_id, from_principal, lease_id
        )

        return await self.receipts.create(
            tenant_id=tenant_id,
            receipt_type=receipt_type,
            from_principal=from_principal,
            to_principal=to_principal,
            task_id=task_id,
            lease_id=lease_id,
            schedule_id=schedule_id,
            parents=parents,
            body=body,
            receipt_hash=receipt_hash,
        )

    async def _emit_result_ready_receipt(
        self,
        tenant_id: UUID,
        task: Task,
    ) -> Receipt:
        """Emit task.result_ready receipt to task owner."""
        asyncgate_principal = Principal(kind=PrincipalKind.SYSTEM, id="asyncgate")

        return await self._emit_receipt(
            tenant_id=tenant_id,
            receipt_type=ReceiptType.TASK_RESULT_READY,
            from_principal=asyncgate_principal,
            to_principal=task.created_by,
            task_id=task.task_id,
            body=ReceiptBody.task_result_ready(
                status=task.status.value,
                result_payload=task.result.result if task.result else None,
                error=task.result.error if task.result else None,
                artifacts=task.result.artifacts if task.result else None,
            ),
        )

    def _compute_receipt_hash(
        self,
        receipt_type: ReceiptType,
        task_id: UUID | None,
        from_principal: Principal,
        lease_id: UUID | None,
    ) -> str:
        """Compute hash for receipt deduplication."""
        data = {
            "receipt_type": receipt_type.value,
            "task_id": str(task_id) if task_id else None,
            "from_kind": from_principal.kind.value,
            "from_id": from_principal.id,
            "lease_id": str(lease_id) if lease_id else None,
        }
        content = json.dumps(data, sort_keys=True)
        return hashlib.sha256(content.encode()).hexdigest()[:32]

    def _task_to_dict(self, task: Task) -> dict[str, Any]:
        """Convert task to dictionary."""
        result = {
            "task_id": str(task.task_id),
            "type": task.type,
            "payload": task.payload,
            "created_by": {
                "kind": task.created_by.kind.value,
                "id": task.created_by.id,
            },
            "requirements": task.requirements.model_dump() if task.requirements else {},
            "priority": task.priority,
            "status": task.status.value,
            "attempt": task.attempt,
            "max_attempts": task.max_attempts,
            "created_at": task.created_at.isoformat(),
            "updated_at": task.updated_at.isoformat(),
            "next_eligible_at": task.next_eligible_at.isoformat() if task.next_eligible_at else None,
        }

        if task.result:
            result["result"] = {
                "outcome": task.result.outcome.value,
                "result": task.result.result,
                "error": task.result.error,
                "artifacts": task.result.artifacts,
                "completed_at": task.result.completed_at.isoformat(),
            }

        return result

    def _task_to_summary(self, task: Task) -> TaskSummary:
        """Convert task to summary."""
        return TaskSummary(
            task_id=task.task_id,
            type=task.type,
            status=task.status,
            priority=task.priority,
            attempt=task.attempt,
            created_at=task.created_at,
            updated_at=task.updated_at,
            next_eligible_at=task.next_eligible_at,
        )

    def _receipt_to_dict(self, receipt: Receipt) -> dict[str, Any]:
        """Convert receipt to dictionary."""
        return {
            "receipt_id": str(receipt.receipt_id),
            "receipt_type": receipt.receipt_type.value,
            "created_at": receipt.created_at.isoformat(),
            "from": {
                "kind": receipt.from_principal.kind.value,
                "id": receipt.from_principal.id,
            },
            "to": {
                "kind": receipt.to_principal.kind.value,
                "id": receipt.to_principal.id,
            },
            "task_id": str(receipt.task_id) if receipt.task_id else None,
            "lease_id": str(receipt.lease_id) if receipt.lease_id else None,
            "parents": [str(p) for p in receipt.parents],
            "body": receipt.body,
            "delivered_at": receipt.delivered_at.isoformat() if receipt.delivered_at else None,
        }
