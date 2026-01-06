"""AsyncGate core engine - canonical operations."""

import asyncio
import hashlib
import json
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from asyncgate.config import settings
from asyncgate.db.repositories import (
    LeaseRepository,
    ProgressRepository,
    ReceiptRepository,
    RelationshipRepository,
    TaskRepository,
)
from asyncgate.db.tables import TaskTable
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
        DEPRECATED: Use list_open_obligations() instead.
        
        Legacy bootstrap with attention semantics. Simplified to remove
        task-state queries (wrong model). Returns minimal response for
        API compatibility while clients migrate to /v1/obligations/open.
        
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

        # Mark receipts as delivered (telemetry only - not control logic)
        if inbox_receipts:
            receipt_ids = [r.receipt_id for r in inbox_receipts]
            await self.receipts.mark_delivered(tenant_id, receipt_ids)

        # Build anomalies (from receipts or detected conditions)
        anomalies = [
            r.body for r in inbox_receipts
            if r.receipt_type == ReceiptType.SYSTEM_ANOMALY
        ]

        # DEPRECATED: Task-state bucketing removed (Tier 3 cleanup)
        # Clients should migrate to /v1/obligations/open for correct model.
        # Return empty lists for backward compatibility.

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
                "assigned_tasks": [],  # REMOVED: Use /v1/obligations/open
                "waiting_results": [],  # REMOVED: Use /v1/obligations/open
                "running_or_scheduled": [],  # REMOVED: Use /v1/obligations/open
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

        return {"task_id": task.task_id, "status": task.status.value}

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
        """
        Cancel a task.
        
        P0.2: All state changes + receipt emissions are atomic via savepoint.
        """
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

        # P0.2: ATOMIC BLOCK - Lease release + task cancellation + receipt
        async with self.session.begin_nested():  # SAVEPOINT
            # 1. Release any active lease
            await self.leases.release(tenant_id, task_id)

            # 2. Cancel the task
            task = await self.tasks.cancel(tenant_id, task_id, reason)

            # 3. Emit result_ready receipt to owner
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
                    "task_id": task.task_id,
                    "lease_id": lease.lease_id,
                    "type": task.type,
                    "payload": task.payload,
                    "attempt": task.attempt,
                    "expires_at": lease.expires_at,
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

        if task.status != TaskStatus.LEASED:
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

        return {"ok": True, "expires_at": lease.expires_at}

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
        """
        Mark a task as successfully completed.
        
        P0.2: All state changes + receipt emissions are atomic via savepoint.
        If any operation fails, entire transaction rolls back.
        """
        # Validate lease (outside transaction - read-only check)
        lease = await self.leases.validate(tenant_id, task_id, lease_id, worker_id)
        if not lease:
            raise LeaseInvalidOrExpired(str(task_id), str(lease_id))

        task = await self.tasks.get(tenant_id, task_id)
        if not task:
            raise TaskNotFound(str(task_id))

        if not task.can_transition_to(TaskStatus.SUCCEEDED):
            raise InvalidStateTransition(task.status.value, TaskStatus.SUCCEEDED.value)

        # P0.2: ATOMIC BLOCK - All or nothing
        async with self.session.begin_nested():  # SAVEPOINT
            # 1. Update task to succeeded
            task_result = TaskResult(
                outcome=Outcome.SUCCEEDED,
                result=result,
                artifacts=artifacts,
                completed_at=datetime.now(timezone.utc),
            )
            await self.tasks.update_status(tenant_id, task_id, TaskStatus.SUCCEEDED, task_result)

            # 2. Release lease
            await self.leases.release(tenant_id, task_id)

            # 3. Emit task.completed receipt
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

            # 4. Emit result_ready to task owner
            task = await self.tasks.get(tenant_id, task_id)
            await self._emit_result_ready_receipt(tenant_id, task)
        
        # If we reach here, all operations succeeded and were committed
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
        
        P0.2: All state changes + receipt emissions are atomic via savepoint.
        Handles both requeue path and terminal failure path atomically.

        If retryable and attempts remaining, requeue with backoff.
        Otherwise mark as terminal failure.
        """
        # Validate lease (outside transaction - read-only check)
        lease = await self.leases.validate(tenant_id, task_id, lease_id, worker_id)
        if not lease:
            raise LeaseInvalidOrExpired(str(task_id), str(lease_id))

        task = await self.tasks.get(tenant_id, task_id)
        if not task:
            raise TaskNotFound(str(task_id))

        # Check if should retry (decision logic outside transaction)
        should_requeue = retryable and (task.attempt + 1) < task.max_attempts

        # P0.2: ATOMIC BLOCK - All state changes + receipts
        async with self.session.begin_nested():  # SAVEPOINT
            # 1. Release lease first (common to both paths)
            await self.leases.release(tenant_id, task_id)

            if should_requeue:
                # REQUEUE PATH: Task gets another attempt
                task = await self.tasks.requeue_with_backoff(tenant_id, task_id, increment_attempt=True)
                next_eligible_at = task.next_eligible_at
                
                # Emit task.requeued receipt to task owner (agent-visible)
                asyncgate_principal = Principal(kind=PrincipalKind.SYSTEM, id="asyncgate")
                await self._emit_receipt(
                    tenant_id=tenant_id,
                    receipt_type=ReceiptType.TASK_FAILED,
                    from_principal=asyncgate_principal,
                    to_principal=task.created_by,
                    task_id=task_id,
                    body={
                        "reason": "Worker reported retryable failure",
                        "error": error,
                        "requeued": True,
                        "attempt": task.attempt,
                        "max_attempts": task.max_attempts,
                        "next_eligible_at": next_eligible_at.isoformat() if next_eligible_at else None,
                    },
                )
            else:
                # TERMINAL FAILURE PATH: No more retries
                task_result = TaskResult(
                    outcome=Outcome.FAILED,
                    error=error,
                    completed_at=datetime.now(timezone.utc),
                )
                await self.tasks.update_status(tenant_id, task_id, TaskStatus.FAILED, task_result)

                # Emit result_ready to task owner
                task = await self.tasks.get(tenant_id, task_id)
                await self._emit_result_ready_receipt(tenant_id, task)
                
                next_eligible_at = None

            # 2. Emit worker's task.failed receipt (system record, common to both paths)
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
        
        # If we reach here, all operations succeeded and were committed
        return {
            "ok": True,
            "requeued": should_requeue,
            "next_eligible_at": next_eligible_at,
        }

    # =========================================================================
    # System Operations
    # =========================================================================

    async def expire_leases(self, batch_size: int = 20) -> int:
        """
        Expire stale leases and requeue tasks with anti-storm protections.

        Called by background sweep task. Only processes leases for tasks
        owned by this AsyncGate instance (multi-instance safe).
        
        CRITICAL: Uses requeue_on_expiry() which does NOT increment attempt.
        Lease expiry is "lost authority" (worker crash), not "task failed".
        
        P0.2: Each lease expiry is atomic - requeue + lease release + receipt
        all succeed or all rollback.
        
        Args:
            batch_size: Number of leases to process per batch (default 20).
                       Smaller batches with jittered requeue times prevent
                       thundering herd when many leases expire simultaneously.
        
        Returns:
            Total number of expired leases processed.
        """
        import random
        
        expired_leases = await self.leases.get_expired(
            limit=100, 
            instance_id=settings.instance_id
        )
        count = 0

        for lease in expired_leases:
            task = await self.tasks.get(lease.tenant_id, lease.task_id)
            if not task or task.is_terminal():
                continue

            # Add jitter to requeue time: 0-5 seconds random delay
            # This prevents all expired tasks from becoming eligible simultaneously
            jitter_seconds = random.uniform(0, 5)
            
            # P0.2: ATOMIC BLOCK - Each lease expiry is atomic
            try:
                async with self.session.begin_nested():  # SAVEPOINT
                    # 1. CRITICAL: Use requeue_on_expiry (does NOT increment attempt)
                    # Lease expiry = "lost authority", NOT "task failed"
                    await self.tasks.requeue_on_expiry(
                        lease.tenant_id,
                        lease.task_id,
                        jitter_seconds=jitter_seconds,
                    )

                    # 2. Release expired lease
                    await self.leases.release(lease.tenant_id, lease.task_id)

                    # 3. Emit lease.expired receipt to task owner
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
            except Exception as e:
                # Log but continue processing other leases
                import logging
                logger = logging.getLogger("asyncgate.engine")
                logger.error(f"Failed to expire lease {lease.lease_id}: {e}", exc_info=True)
                continue
            
            # Batch processing: commit and pause between batches
            if count % batch_size == 0:
                await self.session.commit()
                # Small pause between batches (10-50ms) to avoid transaction pile-up
                await asyncio.sleep(random.uniform(0.01, 0.05))

        return count

    # =========================================================================
    # Receipt Query Operations (Tier 0 - Foundation)
    # =========================================================================

    async def get_receipt(
        self,
        tenant_id: UUID,
        receipt_id: UUID,
    ) -> dict[str, Any] | None:
        """
        Get a specific receipt by ID.
        
        Used for receipt chain traversal and obligation verification.
        """
        receipt = await self.receipts.get_by_id(tenant_id, receipt_id)
        if not receipt:
            return None
        return self._receipt_to_dict(receipt)

    async def list_receipts_by_parent(
        self,
        tenant_id: UUID,
        parent_receipt_id: UUID,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """
        List all receipts that reference a specific parent.
        
        Used by agents to find terminal receipts (may include retries/duplicates).
        """
        receipts = await self.receipts.get_by_parent(
            tenant_id, parent_receipt_id, limit
        )
        return [self._receipt_to_dict(r) for r in receipts]

    async def get_latest_terminator(
        self,
        tenant_id: UUID,
        parent_receipt_id: UUID,
    ) -> dict[str, Any] | None:
        """
        Get the most recent terminator for a parent receipt.
        
        Simplifies agent logic: when multiple terminators exist (retries, duplicates),
        return the canonical one (most recent).
        """
        receipt = await self.receipts.get_latest_terminator(
            tenant_id, parent_receipt_id
        )
        if not receipt:
            return None
        return self._receipt_to_dict(receipt)

    async def has_terminator(
        self,
        tenant_id: UUID,
        parent_receipt_id: UUID,
    ) -> bool:
        """
        Fast check: does a terminator exist for this obligation?
        
        DB-driven: O(1) EXISTS query, doesn't load receipt data.
        """
        return await self.receipts.has_terminator(tenant_id, parent_receipt_id)

    async def list_open_obligations(
        self,
        tenant_id: UUID,
        principal: Principal,
        since_receipt_id: UUID | None = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        """
        List open obligations for a principal.
        
        This is the foundation for the new bootstrap model:
        - Returns receipts that create obligations (task.assigned, etc.)
        - Filters to only those without terminal child receipts
        - Pure ledger dump, no bucketing or interpretation
        
        An obligation is "open" if no terminator receipt exists that references
        it as a parent. Termination is detected via DB, not semantic inference.
        """
        obligations, next_cursor = await self.receipts.list_open_obligations(
            tenant_id=tenant_id,
            to_kind=principal.kind,
            to_id=principal.id,
            since_receipt_id=since_receipt_id,
            limit=limit,
        )

        return {
            "open_obligations": [self._receipt_to_dict(r) for r in obligations],
            "cursor": str(next_cursor) if next_cursor else None,
        }

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
        # Compute hash for idempotency (includes all identifying fields)
        receipt_hash = self._compute_receipt_hash(
            receipt_type=receipt_type,
            task_id=task_id,
            from_principal=from_principal,
            to_principal=to_principal,
            lease_id=lease_id,
            body=body,
            parents=parents,  # P0.5: Include parents in hash
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
        to_principal: Principal,
        lease_id: UUID | None,
        body: dict[str, Any] | None,
        parents: list[UUID] | None,  # P0.5: Include parents in hash
    ) -> str:
        """
        Compute hash for receipt deduplication.
        
        P0.5: Parents are now included in hash to prevent collisions
        where same body but different parents would dedupe incorrectly.
        
        Includes all fields that make a receipt unique:
        - receipt_type, task_id, lease_id
        - from (kind + id)
        - to (kind + id)
        - parents (sorted list of UUID strings)
        - body (canonical JSON)
        
        Returns full 64-character SHA256 hex digest.
        """
        # Create canonical body hash if body exists
        body_hash = None
        if body:
            # P1.3: Use canonical JSON with separators for stability
            body_canonical = json.dumps(body, sort_keys=True, separators=(',', ':'))
            body_hash = hashlib.sha256(body_canonical.encode()).hexdigest()
        
        # Build receipt key from all identifying fields INCLUDING PARENTS
        data = {
            "receipt_type": receipt_type.value,
            "task_id": str(task_id) if task_id else None,
            "from_kind": from_principal.kind.value,
            "from_id": from_principal.id,
            "to_kind": to_principal.kind.value,
            "to_id": to_principal.id,
            "lease_id": str(lease_id) if lease_id else None,
            "parents": sorted([str(p) for p in (parents or [])]),  # P0.5: Include parents
            "body_hash": body_hash,
        }
        # P1.3: Use canonical JSON serialization
        content = json.dumps(data, sort_keys=True, separators=(',', ':'))
        # Return full 64-char hex digest (no truncation)
        return hashlib.sha256(content.encode()).hexdigest()

    def _task_to_dict(self, task: Task) -> dict[str, Any]:
        """Convert task to dictionary with native types."""
        result = {
            "task_id": task.task_id,
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
            "created_at": task.created_at,
            "updated_at": task.updated_at,
            "next_eligible_at": task.next_eligible_at,
        }

        if task.result:
            result["result"] = {
                "outcome": task.result.outcome.value,
                "result": task.result.result,
                "error": task.result.error,
                "artifacts": task.result.artifacts,
                "completed_at": task.result.completed_at,
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
        """Convert receipt to dictionary with native types."""
        return {
            "receipt_id": receipt.receipt_id,
            "receipt_type": receipt.receipt_type.value,
            "created_at": receipt.created_at,
            "from": {
                "kind": receipt.from_.kind.value,
                "id": receipt.from_.id,
            },
            "to": {
                "kind": receipt.to_.kind.value,
                "id": receipt.to_.id,
            },
            "task_id": receipt.task_id,
            "lease_id": receipt.lease_id,
            "parents": receipt.parents,
            "body": receipt.body,
            "delivered_at": receipt.delivered_at,
        }
