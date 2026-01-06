"""REST API router."""

import json
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from asyncgate.api.schemas import (
    AckReceiptRequest,
    AckReceiptResponse,
    BootstrapRequest,
    CancelTaskRequest,
    CancelTaskResponse,
    CompleteTaskRequest,
    CompleteTaskResponse,
    ConfigResponse,
    CreateTaskRequest,
    CreateTaskResponse,
    FailTaskRequest,
    FailTaskResponse,
    HealthResponse,
    LeaseClaimRequest,
    LeaseClaimResponse,
    ListReceiptsResponse,
    ListTasksResponse,
    OpenObligationsResponse,
    RenewLeaseRequest,
    RenewLeaseResponse,
    ReportProgressRequest,
    ReportProgressResponse,
    TaskResponse,
)
from asyncgate.api.deps import get_db_session, get_tenant_id, verify_api_key
from asyncgate.engine import (
    AsyncGateEngine,
    InvalidStateTransition,
    LeaseInvalidOrExpired,
    TaskNotFound,
    UnauthorizedError,
)
from asyncgate.models import Principal, PrincipalKind

router = APIRouter(prefix="/v1", dependencies=[Depends(verify_api_key)])


# ============================================================================
# Health & Config
# ============================================================================


@router.get("/health", response_model=HealthResponse)
async def health_check():
    """Health check endpoint."""
    return HealthResponse(status="healthy", version="0.1.0")


@router.get("/config", response_model=ConfigResponse)
async def get_config(
    session: AsyncSession = Depends(get_db_session),
):
    """Get server configuration."""
    engine = AsyncGateEngine(session)
    config = await engine.get_config()
    return ConfigResponse(**config)


# ============================================================================
# Obligations (New Bootstrap Model)
# ============================================================================


@router.get("/obligations/open", response_model=OpenObligationsResponse)
async def get_open_obligations(
    principal_kind: str = Query(...),
    principal_id: str = Query(...),
    principal_instance_id: Optional[str] = Query(None),
    since_receipt_id: Optional[UUID] = Query(None),
    limit: Optional[int] = Query(None, ge=1, le=200),
    session: AsyncSession = Depends(get_db_session),
    tenant_id: UUID = Depends(get_tenant_id),
):
    """
    Get open obligations for a principal (obligation ledger model).
    
    Returns uncommitted obligations from the receipt ledger. An obligation
    is "open" if no terminator receipt exists that references it as a parent.
    
    This is the canonical bootstrap endpoint - pure ledger dump with no
    bucketing, attention semantics, or task state interpretation.
    """
    from fastapi import Response
    
    engine = AsyncGateEngine(session)

    principal = Principal(
        kind=PrincipalKind(principal_kind),
        id=principal_id,
        instance_id=principal_instance_id,
    )

    # Update relationship (same as old bootstrap for continuity)
    relationship = await engine.relationships.upsert(
        tenant_id=tenant_id,
        principal_kind=principal.kind,
        principal_id=principal.id,
        principal_instance_id=principal.instance_id,
    )

    # Get open obligations (the new truth)
    limit_value = min(
        limit or 50,
        200,
    )
    
    obligations_data = await engine.list_open_obligations(
        tenant_id=tenant_id,
        principal=principal,
        since_receipt_id=since_receipt_id,
        limit=limit_value,
    )

    # Get config for server metadata
    from asyncgate.config import settings
    
    return OpenObligationsResponse(
        server={
            "name": "AsyncGate",
            "version": "0.1.0",
            "instance_id": settings.instance_id,
            "environment": settings.env.value,
        },
        relationship={
            "principal_kind": relationship.principal_kind.value,
            "principal_id": relationship.principal_id,
            "principal_instance_id": relationship.principal_instance_id,
            "first_seen_at": relationship.first_seen_at.isoformat(),
            "last_seen_at": relationship.last_seen_at.isoformat(),
            "sessions_count": relationship.sessions_count,
        },
        open_obligations=obligations_data["open_obligations"],
        cursor=obligations_data.get("cursor"),
    )


# ============================================================================
# Bootstrap (DEPRECATED - use /obligations/open)
# ============================================================================


@router.get("/bootstrap")
async def bootstrap(
    principal_kind: str = Query(...),
    principal_id: str = Query(...),
    principal_instance_id: Optional[str] = Query(None),
    since_receipt_id: Optional[UUID] = Query(None),
    max_items: Optional[int] = Query(None, ge=1, le=200),
    session: AsyncSession = Depends(get_db_session),
    tenant_id: UUID = Depends(get_tenant_id),
):
    """
    Bootstrap session and get attention-aware status.
    
    DEPRECATED: Use /v1/obligations/open instead.
    This endpoint will be removed in a future version.
    """
    from fastapi import Response
    from starlette.background import BackgroundTask
    import logging
    
    logger = logging.getLogger(__name__)
    logger.warning(
        f"DEPRECATED: /v1/bootstrap called by principal {principal_kind}:{principal_id}. "
        "Use /v1/obligations/open instead."
    )
    
    engine = AsyncGateEngine(session)

    principal = Principal(
        kind=PrincipalKind(principal_kind),
        id=principal_id,
        instance_id=principal_instance_id,
    )

    result = await engine.bootstrap(
        tenant_id=tenant_id,
        principal=principal,
        since_receipt_id=since_receipt_id,
        max_items=max_items,
    )
    
    # Add deprecation header
    from fastapi.encoders import jsonable_encoder
    
    return Response(
        content=json.dumps(jsonable_encoder(result)),
        media_type="application/json",
        headers={
            "X-AsyncGate-Deprecated": "Use /v1/obligations/open instead",
            "Deprecation": "true",
        },
    )


# ============================================================================
# TASKER Endpoints
# ============================================================================


@router.post("/tasks", response_model=CreateTaskResponse)
async def create_task(
    request: CreateTaskRequest,
    principal_kind: str = Query("agent"),
    principal_id: str = Query(...),
    session: AsyncSession = Depends(get_db_session),
    tenant_id: UUID = Depends(get_tenant_id),
):
    """Create a new task."""
    engine = AsyncGateEngine(session)

    created_by = Principal(
        kind=PrincipalKind(principal_kind),
        id=principal_id,
    )

    result = await engine.create_task(
        tenant_id=tenant_id,
        type=request.type,
        payload=request.payload,
        created_by=created_by,
        requirements=request.requirements,
        priority=request.priority,
        idempotency_key=request.idempotency_key,
        max_attempts=request.max_attempts,
        retry_backoff_seconds=request.retry_backoff_seconds,
        delay_seconds=request.delay_seconds,
    )

    return CreateTaskResponse(**result)


@router.get("/tasks/{task_id}")
async def get_task(
    task_id: UUID,
    session: AsyncSession = Depends(get_db_session),
    tenant_id: UUID = Depends(get_tenant_id),
):
    """Get a task by ID."""
    engine = AsyncGateEngine(session)

    try:
        return await engine.get_task(tenant_id, task_id)
    except TaskNotFound as e:
        raise HTTPException(status_code=404, detail=e.message)


@router.get("/tasks", response_model=ListTasksResponse)
async def list_tasks(
    status: Optional[str] = Query(None),
    type: Optional[str] = Query(None),
    created_by: Optional[str] = Query(None),
    limit: Optional[int] = Query(None, ge=1, le=200),
    cursor: Optional[str] = Query(None),
    session: AsyncSession = Depends(get_db_session),
    tenant_id: UUID = Depends(get_tenant_id),
):
    """List tasks with optional filtering."""
    engine = AsyncGateEngine(session)

    result = await engine.list_tasks(
        tenant_id=tenant_id,
        status=status,
        type=type,
        created_by_id=created_by,
        limit=limit,
        cursor=cursor,
    )

    return ListTasksResponse(**result)


@router.post("/tasks/{task_id}/cancel", response_model=CancelTaskResponse)
async def cancel_task(
    task_id: UUID,
    request: CancelTaskRequest,
    session: AsyncSession = Depends(get_db_session),
    tenant_id: UUID = Depends(get_tenant_id),
):
    """Cancel a task."""
    engine = AsyncGateEngine(session)

    principal = Principal(
        kind=PrincipalKind(request.principal_kind),
        id=request.principal_id,
    )

    try:
        result = await engine.cancel_task(
            tenant_id=tenant_id,
            task_id=task_id,
            principal=principal,
            reason=request.reason,
        )
        return CancelTaskResponse(**result)
    except TaskNotFound as e:
        raise HTTPException(status_code=404, detail=e.message)
    except UnauthorizedError as e:
        raise HTTPException(status_code=403, detail=e.message)


# ============================================================================
# Receipts
# ============================================================================


@router.get("/receipts", response_model=ListReceiptsResponse)
async def list_receipts(
    to_kind: str = Query(...),
    to_id: str = Query(...),
    since_receipt_id: Optional[UUID] = Query(None),
    limit: Optional[int] = Query(None, ge=1, le=200),
    session: AsyncSession = Depends(get_db_session),
    tenant_id: UUID = Depends(get_tenant_id),
):
    """List receipts for a principal."""
    engine = AsyncGateEngine(session)

    result = await engine.list_receipts(
        tenant_id=tenant_id,
        to_kind=to_kind,
        to_id=to_id,
        since_receipt_id=since_receipt_id,
        limit=limit,
    )

    return ListReceiptsResponse(**result)


@router.post("/receipts/{receipt_id}/ack", response_model=AckReceiptResponse)
async def ack_receipt(
    receipt_id: UUID,
    request: AckReceiptRequest,
    session: AsyncSession = Depends(get_db_session),
    tenant_id: UUID = Depends(get_tenant_id),
):
    """Acknowledge a receipt."""
    engine = AsyncGateEngine(session)

    principal = Principal(
        kind=PrincipalKind(request.principal_kind),
        id=request.principal_id,
    )

    result = await engine.ack_receipt(
        tenant_id=tenant_id,
        receipt_id=receipt_id,
        principal=principal,
    )

    return AckReceiptResponse(**result)


# ============================================================================
# TASKEE Endpoints
# ============================================================================


@router.post("/leases/claim", response_model=LeaseClaimResponse)
async def lease_claim(
    request: LeaseClaimRequest,
    session: AsyncSession = Depends(get_db_session),
    tenant_id: UUID = Depends(get_tenant_id),
):
    """Claim next available tasks."""
    engine = AsyncGateEngine(session)

    result = await engine.lease_next(
        tenant_id=tenant_id,
        worker_id=request.worker_id,
        capabilities=request.capabilities,
        accept_types=request.accept_types,
        max_tasks=request.max_tasks or 1,
        lease_ttl_seconds=request.lease_ttl_seconds,
    )

    return LeaseClaimResponse(tasks=result["tasks"])


@router.post("/leases/renew", response_model=RenewLeaseResponse)
async def renew_lease(
    request: RenewLeaseRequest,
    session: AsyncSession = Depends(get_db_session),
    tenant_id: UUID = Depends(get_tenant_id),
):
    """Renew a lease."""
    engine = AsyncGateEngine(session)

    try:
        result = await engine.renew_lease(
            tenant_id=tenant_id,
            worker_id=request.worker_id,
            task_id=request.task_id,
            lease_id=request.lease_id,
            extend_by_seconds=request.extend_by_seconds,
        )
        return RenewLeaseResponse(**result)
    except (TaskNotFound, LeaseInvalidOrExpired) as e:
        raise HTTPException(status_code=400, detail=e.message)


@router.post("/tasks/{task_id}/progress", response_model=ReportProgressResponse)
async def report_progress(
    task_id: UUID,
    request: ReportProgressRequest,
    session: AsyncSession = Depends(get_db_session),
    tenant_id: UUID = Depends(get_tenant_id),
):
    """Report task progress."""
    engine = AsyncGateEngine(session)

    try:
        result = await engine.report_progress(
            tenant_id=tenant_id,
            worker_id=request.worker_id,
            task_id=task_id,
            lease_id=request.lease_id,
            progress_data=request.progress,
        )
        return ReportProgressResponse(**result)
    except LeaseInvalidOrExpired as e:
        raise HTTPException(status_code=400, detail=e.message)


@router.post("/tasks/{task_id}/complete", response_model=CompleteTaskResponse)
async def complete_task(
    task_id: UUID,
    request: CompleteTaskRequest,
    session: AsyncSession = Depends(get_db_session),
    tenant_id: UUID = Depends(get_tenant_id),
):
    """Mark task as completed."""
    engine = AsyncGateEngine(session)

    try:
        result = await engine.complete(
            tenant_id=tenant_id,
            worker_id=request.worker_id,
            task_id=task_id,
            lease_id=request.lease_id,
            result=request.result,
            artifacts=request.artifacts,
        )
        return CompleteTaskResponse(**result)
    except (TaskNotFound, LeaseInvalidOrExpired, InvalidStateTransition) as e:
        raise HTTPException(status_code=400, detail=e.message)


@router.post("/tasks/{task_id}/fail", response_model=FailTaskResponse)
async def fail_task(
    task_id: UUID,
    request: FailTaskRequest,
    session: AsyncSession = Depends(get_db_session),
    tenant_id: UUID = Depends(get_tenant_id),
):
    """Mark task as failed."""
    engine = AsyncGateEngine(session)

    try:
        result = await engine.fail(
            tenant_id=tenant_id,
            worker_id=request.worker_id,
            task_id=task_id,
            lease_id=request.lease_id,
            error=request.error,
            retryable=request.retryable,
        )
        return FailTaskResponse(**result)
    except (TaskNotFound, LeaseInvalidOrExpired) as e:
        raise HTTPException(status_code=400, detail=e.message)
