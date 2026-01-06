# AsyncGate Test Specifications

## Overview

This document defines comprehensive test specifications for the AsyncGate obligation ledger model. Tests validate the architectural realignment from "attention inbox" to "obligation ledger" patterns.

## Test Priority Levels

- **P0 (Critical):** Core obligation model, prevents data loss/corruption
- **P1 (High):** Prevents production footguns, ensures correctness
- **P2 (Medium):** Performance, edge cases
- **P3 (Low):** Nice-to-have validations

## T6.1: Termination Logic Tests (P0)

### Test: Type Semantics Match

```python
async def test_termination_type_semantics():
    """Validate TERMINATION_RULES truth table is correct."""
    from asyncgate.models.termination import (
        TERMINATION_RULES,
        get_terminal_types,
        can_terminate_type,
    )
    
    # task.assigned can be terminated by these types
    terminators = get_terminal_types(ReceiptType.TASK_ASSIGNED)
    assert ReceiptType.TASK_COMPLETED in terminators
    assert ReceiptType.TASK_FAILED in terminators
    assert ReceiptType.TASK_CANCELED in terminators
    
    # task.completed is terminal (can terminate but not be terminated)
    assert can_terminate_type(
        ReceiptType.TASK_COMPLETED,
        ReceiptType.TASK_ASSIGNED
    )
    assert ReceiptType.TASK_COMPLETED not in get_terminal_types(ReceiptType.TASK_COMPLETED)
```

### Test: DB-Driven Termination Check

```python
async def test_has_terminator_db_query():
    """Verify has_terminator uses O(1) EXISTS query, not ledger scan."""
    # Create obligation
    assign = await receipts.create(
        receipt_type=ReceiptType.TASK_ASSIGNED,
        ...
    )
    
    # Check: No terminator yet
    has_term = await receipts.has_terminator(tenant_id, assign.receipt_id)
    assert has_term is False
    
    # Create terminator
    complete = await receipts.create(
        receipt_type=ReceiptType.TASK_COMPLETED,
        parents=[assign.receipt_id],
        ...
    )
    
    # Check: Terminator exists
    has_term = await receipts.has_terminator(tenant_id, assign.receipt_id)
    assert has_term is True
    
    # Validate query plan (if possible): Should use EXISTS, not full scan
```

### Test: Obligation Chain Detection

```python
async def test_partial_chain_stays_open():
    """Obligation without terminal receipt stays in open_obligations."""
    # Create obligation
    assign = await receipts.create(
        receipt_type=ReceiptType.TASK_ASSIGNED,
        to_principal=agent,
        ...
    )
    
    # Query open obligations
    result = await engine.list_open_obligations(
        tenant_id=tenant_id,
        principal=agent,
    )
    
    obligations = result["open_obligations"]
    assert len(obligations) == 1
    assert obligations[0]["receipt_id"] == str(assign.receipt_id)
```

## T6.2: Parent Linkage Tests (P0)

### Test: Terminal Without Parents Fails

```python
async def test_terminal_receipt_requires_parents():
    """Terminal receipt without parents raises ValueError."""
    with pytest.raises(ValueError, match="must specify parents"):
        await receipts.create(
            receipt_type=ReceiptType.TASK_COMPLETED,  # Terminal type
            parents=[],  # Empty!
            ...
        )
    
    with pytest.raises(ValueError, match="must specify parents"):
        await receipts.create(
            receipt_type=ReceiptType.TASK_COMPLETED,
            parents=None,  # None!
            ...
        )
```

### Test: Parent Existence Validation

```python
async def test_parent_must_exist():
    """Parent receipt must exist in same tenant."""
    fake_parent_id = uuid4()
    
    with pytest.raises(ValueError, match="Parent receipt.*not found"):
        await receipts.create(
            receipt_type=ReceiptType.TASK_COMPLETED,
            parents=[fake_parent_id],  # Doesn't exist
            ...
        )
```

### Test: Cross-Tenant Parent Rejected

```python
async def test_cross_tenant_parent_rejected():
    """Parent from different tenant is rejected."""
    # Create receipt in tenant A
    assign = await receipts.create(
        tenant_id=tenant_a,
        receipt_type=ReceiptType.TASK_ASSIGNED,
        ...
    )
    
    # Try to reference from tenant B
    with pytest.raises(ValueError, match="not found for tenant"):
        await receipts.create(
            tenant_id=tenant_b,  # Different tenant
            receipt_type=ReceiptType.TASK_COMPLETED,
            parents=[assign.receipt_id],  # From tenant A
            ...
        )
```

### Test: Different Actors Discharge Obligations

```python
async def test_cross_actor_obligation_discharge():
    """Worker can discharge agent's obligation (different actors OK)."""
    # Agent gets obligation
    assign = await receipts.create(
        receipt_type=ReceiptType.TASK_ASSIGNED,
        to_principal=Principal(kind="agent", id="alice"),
        ...
    )
    
    # Worker discharges it (different actor)
    complete = await receipts.create(
        receipt_type=ReceiptType.TASK_COMPLETED,
        from_principal=Principal(kind="worker", id="bob"),
        to_principal=Principal(kind="agent", id="alice"),
        parents=[assign.receipt_id],
        body={"artifacts": [...]},
    )
    
    # Should succeed - different actors is a feature, not a bug
    assert complete is not None
    
    # Obligation discharged
    has_term = await receipts.has_terminator(tenant_id, assign.receipt_id)
    assert has_term is True
```

## T6.3: Locatability Tests (P0)

### Test: Success Without Locatability Strips Parents

```python
async def test_success_without_locatability_phase1():
    """Success without artifacts/delivery_proof strips parents (lenient)."""
    # Create obligation
    assign = await receipts.create(
        receipt_type=ReceiptType.TASK_ASSIGNED,
        ...
    )
    
    # Try to complete without locatability
    complete = await receipts.create(
        receipt_type=ReceiptType.TASK_COMPLETED,
        parents=[assign.receipt_id],
        body={
            "result_summary": "Done"
            # No artifacts, no delivery_proof
        }
    )
    
    # Phase 1 (lenient): Receipt created but parents stripped
    assert complete is not None
    assert complete.parents == []  # ← Stripped!
    
    # Obligation STAYS OPEN
    has_term = await receipts.has_terminator(tenant_id, assign.receipt_id)
    assert has_term is False
```

### Test: Success With Artifacts Works

```python
async def test_success_with_artifacts():
    """Success with artifacts properly discharges obligation."""
    assign = await receipts.create(
        receipt_type=ReceiptType.TASK_ASSIGNED,
        ...
    )
    
    complete = await receipts.create(
        receipt_type=ReceiptType.TASK_COMPLETED,
        parents=[assign.receipt_id],
        body={
            "result_summary": "Done",
            "artifacts": [
                {"type": "s3", "url": "s3://bucket/key"}
            ]
        }
    )
    
    # Parents preserved
    assert complete.parents == [assign.receipt_id]
    
    # Obligation discharged
    has_term = await receipts.has_terminator(tenant_id, assign.receipt_id)
    assert has_term is True
```

### Test: Success With Delivery Proof Works

```python
async def test_success_with_delivery_proof():
    """Success with delivery_proof properly discharges obligation."""
    assign = await receipts.create(
        receipt_type=ReceiptType.TASK_ASSIGNED,
        ...
    )
    
    complete = await receipts.create(
        receipt_type=ReceiptType.TASK_COMPLETED,
        parents=[assign.receipt_id],
        body={
            "result_summary": "Delivered",
            "delivery_proof": {
                "mode": "push",
                "target": {"endpoint": "https://..."},
                "status": "succeeded",
                "at": "2026-01-05T12:00:00Z",
                "proof": {"request_id": "req_123"}
            }
        }
    )
    
    assert complete.parents == [assign.receipt_id]
    has_term = await receipts.has_terminator(tenant_id, assign.receipt_id)
    assert has_term is True
```

## T6.4: Bootstrap Obligations Tests (P0)

### Test: Pagination Works

```python
async def test_obligations_pagination():
    """since_receipt_id enables cursor-based pagination."""
    # Create 5 obligations
    receipts_list = []
    for i in range(5):
        r = await receipts.create(
            receipt_type=ReceiptType.TASK_ASSIGNED,
            to_principal=agent,
            ...
        )
        receipts_list.append(r)
    
    # Get first 2
    result1 = await engine.list_open_obligations(
        tenant_id=tenant_id,
        principal=agent,
        limit=2,
    )
    assert len(result1["open_obligations"]) == 2
    cursor1 = result1["cursor"]
    
    # Get next 2
    result2 = await engine.list_open_obligations(
        tenant_id=tenant_id,
        principal=agent,
        since_receipt_id=UUID(cursor1),
        limit=2,
    )
    assert len(result2["open_obligations"]) == 2
    
    # No overlap
    ids1 = {o["receipt_id"] for o in result1["open_obligations"]}
    ids2 = {o["receipt_id"] for o in result2["open_obligations"]}
    assert ids1.isdisjoint(ids2)
```

### Test: Filters to Principal

```python
async def test_obligations_filtered_to_principal():
    """Only obligations for requested principal are returned."""
    # Create obligations for alice
    await receipts.create(
        receipt_type=ReceiptType.TASK_ASSIGNED,
        to_principal=Principal(kind="agent", id="alice"),
        ...
    )
    
    # Create obligations for bob
    await receipts.create(
        receipt_type=ReceiptType.TASK_ASSIGNED,
        to_principal=Principal(kind="agent", id="bob"),
        ...
    )
    
    # Query alice's obligations
    result = await engine.list_open_obligations(
        tenant_id=tenant_id,
        principal=Principal(kind="agent", id="alice"),
    )
    
    # Only alice's obligations
    assert len(result["open_obligations"]) == 1
    assert result["open_obligations"][0]["to"]["id"] == "alice"
```

### Test: Excludes Terminated Obligations

```python
async def test_obligations_excludes_terminated():
    """Terminated obligations don't appear in open_obligations."""
    # Create and terminate obligation
    assign = await receipts.create(
        receipt_type=ReceiptType.TASK_ASSIGNED,
        to_principal=agent,
        ...
    )
    
    # Initially open
    result = await engine.list_open_obligations(
        tenant_id=tenant_id,
        principal=agent,
    )
    assert len(result["open_obligations"]) == 1
    
    # Terminate it
    await receipts.create(
        receipt_type=ReceiptType.TASK_COMPLETED,
        parents=[assign.receipt_id],
        body={"artifacts": [...]},
        ...
    )
    
    # No longer in open obligations
    result = await engine.list_open_obligations(
        tenant_id=tenant_id,
        principal=agent,
    )
    assert len(result["open_obligations"]) == 0
```

## T6.5: Unbucketed Bootstrap Test (P0 - Anti-Regression)

### Test: No Bucketing Fields

```python
async def test_obligations_endpoint_is_unbucketed():
    """
    CRITICAL: /v1/obligations/open returns ONLY unbucketed dump.
    
    This prevents regression to inbox model.
    """
    response = await client.get("/v1/obligations/open", params={
        "principal_kind": "agent",
        "principal_id": "test",
        "limit": 10,
    })
    
    data = response.json()
    
    # MUST have these fields
    assert "server" in data
    assert "relationship" in data
    assert "open_obligations" in data
    assert "cursor" in data
    
    # MUST NOT have bucketing fields
    assert "waiting_results" not in data
    assert "assigned_tasks" not in data
    assert "running_or_scheduled" not in data
    assert "attention" not in data
    assert "anomalies" not in data
    assert "inbox_receipts" not in data
    
    # open_obligations is pure list
    assert isinstance(data["open_obligations"], list)
    
    # No nested categorization
    for obligation in data["open_obligations"]:
        assert "receipt_id" in obligation
        assert "receipt_type" in obligation
        # It's a receipt, not a bucket
```

## T6.6: Lease/Retry Separation Tests (P1)

### Test: Lease Expiry Preserves Attempt

```python
async def test_lease_expiry_does_not_increment_attempt():
    """Lease expiry uses requeue_on_expiry (preserves attempt)."""
    # Create task
    task = await tasks.create(
        type="test",
        payload={},
        created_by=agent,
        max_attempts=3,
        ...
    )
    initial_attempt = task.attempt  # Should be 1
    
    # Claim lease
    lease = await leases.claim(
        task_id=task.task_id,
        worker=worker,
        ...
    )
    
    # Expire lease (simulate worker crash)
    await tasks.requeue_on_expiry(
        tenant_id=tenant_id,
        task_id=task.task_id,
        jitter_seconds=0.0,
    )
    
    # Attempt NOT incremented
    requeued_task = await tasks.get(tenant_id, task.task_id)
    assert requeued_task.attempt == initial_attempt
    assert requeued_task.status == TaskStatus.QUEUED
```

### Test: Real Failure Increments Attempt

```python
async def test_task_failure_increments_attempt():
    """Real failure uses requeue_with_backoff (increments attempt)."""
    task = await tasks.create(
        type="test",
        payload={},
        created_by=agent,
        max_attempts=3,
        ...
    )
    initial_attempt = task.attempt
    
    # Claim lease
    lease = await leases.claim(task_id=task.task_id, ...)
    
    # Fail task (real failure, not lease expiry)
    await tasks.requeue_with_backoff(
        tenant_id=tenant_id,
        task_id=task.task_id,
        increment_attempt=True,
    )
    
    # Attempt incremented
    requeued_task = await tasks.get(tenant_id, task.task_id)
    assert requeued_task.attempt == initial_attempt + 1
```

### Test: No False Terminal Under Flaky Workers

```python
async def test_flaky_worker_doesnt_cause_terminal():
    """Worker crashes don't burn attempts → no false terminal."""
    task = await tasks.create(
        type="test",
        max_attempts=3,
        ...
    )
    
    # Simulate 5 worker crashes (lease expiries)
    for _ in range(5):
        lease = await leases.claim(task_id=task.task_id, ...)
        # Worker crashes - lease expires
        await tasks.requeue_on_expiry(
            tenant_id=tenant_id,
            task_id=task.task_id,
            jitter_seconds=0.0,
        )
    
    # Task still retryable (attempt not burned)
    task = await tasks.get(tenant_id, task.task_id)
    assert task.attempt <= task.max_attempts
    assert task.status == TaskStatus.QUEUED
    
    # Now claim and actually succeed
    lease = await leases.claim(task_id=task.task_id, ...)
    await engine.complete_task(
        tenant_id=tenant_id,
        task_id=task.task_id,
        lease_id=lease.lease_id,
        worker=worker,
        result={...},
        artifacts=[...],
    )
    
    # Task succeeded despite 5 crashes
    task = await tasks.get(tenant_id, task.task_id)
    assert task.status == TaskStatus.SUCCEEDED
```

## T6.7: Receipt Size Limits (P1)

### Test: Body Size Limit

```python
async def test_receipt_body_size_limit():
    """Receipt body larger than 64KB is rejected."""
    large_body = {
        "data": "x" * 70000  # >64KB
    }
    
    with pytest.raises(ValueError, match="Receipt body too large"):
        await receipts.create(
            receipt_type=ReceiptType.TASK_ASSIGNED,
            body=large_body,
            ...
        )
```

### Test: Parents Count Limit

```python
async def test_parents_count_limit():
    """More than 10 parents is rejected."""
    # Create 11 parent receipts
    parent_ids = []
    for _ in range(11):
        r = await receipts.create(
            receipt_type=ReceiptType.TASK_ASSIGNED,
            ...
        )
        parent_ids.append(r.receipt_id)
    
    with pytest.raises(ValueError, match="Too many parent receipts"):
        await receipts.create(
            receipt_type=ReceiptType.TASK_COMPLETED,
            parents=parent_ids,  # 11 parents
            body={"artifacts": [...]},
            ...
        )
```

### Test: Artifacts Count Limit

```python
async def test_artifacts_count_limit():
    """More than 100 artifacts is rejected."""
    artifacts = [
        {"type": "s3", "url": f"s3://bucket/file{i}"}
        for i in range(101)  # 101 artifacts
    ]
    
    with pytest.raises(ValueError, match="Too many artifacts"):
        await receipts.create(
            receipt_type=ReceiptType.TASK_COMPLETED,
            parents=[...],
            body={"artifacts": artifacts},
            ...
        )
```

## Test Infrastructure Recommendations

### Fixtures

```python
@pytest.fixture
async def db_session():
    """Async database session for tests."""
    async with get_session() as session:
        yield session
        await session.rollback()

@pytest.fixture
async def tenant_id():
    """Test tenant ID."""
    return uuid4()

@pytest.fixture
async def agent():
    """Test agent principal."""
    return Principal(kind=PrincipalKind.AGENT, id="test-agent")

@pytest.fixture
async def worker():
    """Test worker principal."""
    return Principal(kind=PrincipalKind.WORKER, id="test-worker")
```

### Test Organization

```
tests/
├── unit/
│   ├── test_termination.py          # T6.1
│   ├── test_parent_linkage.py       # T6.2
│   ├── test_locatability.py         # T6.3
│   ├── test_lease_retry.py          # T6.6
│   └── test_receipt_limits.py       # T6.7
├── integration/
│   ├── test_obligations_api.py      # T6.4, T6.5
│   └── test_receipt_chains.py
└── conftest.py                       # Fixtures
```

## Test Execution Priority

1. **P0 tests first:** Termination, parent linkage, locatability, unbucketed
2. **P1 tests next:** Lease/retry separation, size limits
3. **P2 tests:** Performance, edge cases
4. **P3 tests:** Nice-to-have validations

## Coverage Goals

- **Core obligation model:** 100% coverage
- **Receipt creation/validation:** 100% coverage
- **Termination logic:** 100% coverage
- **API endpoints:** 90% coverage
- **Overall:** 85%+ coverage

## When Tests Are Written

These specifications are ready for implementation when:
- Test framework is set up (pytest-asyncio, fixtures)
- Database migrations are run (test database)
- Async context is configured

Until then, this document serves as:
- Validation checklist for manual testing
- Specification for future automated tests
- Anti-regression contract
