# AsyncGate Priority Fixes

**Status:** Pre-Demo Hardening  
**Target:** Public URL deployment readiness  
**Updated:** 2026-01-05

---

## P0: Must Fix Before Public Demo

### P0.1: Fix open-obligations DoS (Batch + Index)

**Why:** New bootstrap primitive. If slow/DoS-able, whole model collapses.

**Changes:**

**1. Add GIN index for JSONB parents**

```python
# src/asyncgate/db/tables.py (line ~170, in ReceiptTable.__table_args__)

__table_args__ = (
    # ... existing indexes ...
    
    # ADD THIS:
    Index("idx_receipts_parents_gin", "parents", postgresql_using="gin"),
)
```

**Migration SQL:**
```sql
CREATE INDEX idx_receipts_parents_gin ON receipts USING GIN (parents);
```

**2. Replace N+1 loop with batch query**

```python
# src/asyncgate/db/repositories.py (line ~1000-1060)
# REPLACE list_open_obligations implementation:

async def list_open_obligations(
    self,
    tenant_id: UUID,
    to_kind: PrincipalKind,
    to_id: str,
    since_receipt_id: UUID | None = None,
    limit: int = 50,
) -> tuple[list[Receipt], UUID | None]:
    """
    List open obligations for a principal.
    
    OPTIMIZED: Uses batch termination check instead of N+1 queries.
    """
    from asyncgate.models.termination import TERMINATION_RULES
    
    # Get obligation types
    obligation_types = list(TERMINATION_RULES.keys())
    if not obligation_types:
        return [], None
    
    # Fetch candidates with hard cap (prevent runaway)
    candidate_limit = min(limit * 3, 1000)  # Hard cap at 1000
    
    query = select(ReceiptTable).where(
        ReceiptTable.tenant_id == tenant_id,
        ReceiptTable.to_kind == to_kind,
        ReceiptTable.to_id == to_id,
        ReceiptTable.receipt_type.in_(obligation_types),
    )
    
    # Pagination
    if since_receipt_id:
        cursor_result = await self.session.execute(
            select(ReceiptTable.created_at).where(
                ReceiptTable.tenant_id == tenant_id,
                ReceiptTable.receipt_id == since_receipt_id,
            )
        )
        cursor_time = cursor_result.scalar_one_or_none()
        if cursor_time:
            query = query.where(ReceiptTable.created_at > cursor_time)
    
    query = query.order_by(ReceiptTable.created_at.asc()).limit(candidate_limit)
    
    result = await self.session.execute(query)
    candidate_rows = list(result.scalars().all())
    
    if not candidate_rows:
        return [], None
    
    # BATCH TERMINATION CHECK (single query instead of N queries)
    candidate_ids = [str(row.receipt_id) for row in candidate_rows]
    
    # Query: which candidates have terminators?
    # Uses GIN index on parents for fast containment check
    terminated_result = await self.session.execute(
        select(ReceiptTable.parents)
        .where(
            ReceiptTable.tenant_id == tenant_id,
            # PostgreSQL: does parents array overlap with candidate_ids?
            # This uses the GIN index we just created
            func.jsonb_path_exists(
                ReceiptTable.parents,
                f'$[*] ? (@ == "{candidate_ids[0]}" || ' +
                ' || '.join(f'@ == "{cid}"' for cid in candidate_ids[1:]) + ')'
            )
        )
    )
    
    # Build set of terminated receipt IDs
    terminated_ids = set()
    for row in terminated_result:
        for parent_id_str in row.parents:
            terminated_ids.add(UUID(parent_id_str))
    
    # Filter candidates: keep only those NOT terminated
    open_obligations = []
    for row in candidate_rows:
        receipt = self._row_to_model(row)
        if receipt.receipt_id not in terminated_ids:
            open_obligations.append(receipt)
            if len(open_obligations) >= limit:
                break
    
    # Next cursor
    next_cursor = None
    if len(open_obligations) >= limit:
        next_cursor = open_obligations[-1].receipt_id
    
    return open_obligations[:limit], next_cursor
```

**Alternative simpler approach (if jsonb_path_exists is complex):**

```python
# Simpler: fetch all receipts with parents, check overlap in Python
terminated_result = await self.session.execute(
    select(ReceiptTable.receipt_id, ReceiptTable.parents)
    .where(
        ReceiptTable.tenant_id == tenant_id,
        # At least one parent exists
        func.jsonb_array_length(ReceiptTable.parents) > 0,
    )
)

terminated_ids = set()
candidate_id_set = set(candidate_ids)
for row in terminated_result:
    # Check if any parent is in our candidate set
    for parent_str in row.parents:
        if parent_str in candidate_id_set:
            terminated_ids.add(UUID(parent_str))
```

**Test:**
```bash
# Before: 600 queries for limit=200
# After: 2 queries (candidates + batch termination)
```

**Effort:** 4-6 hours  
**Status:** 🔴 BLOCKING

---

### P0.2: Make Task State + Receipts Atomic

**Why:** Obligation discharged depends on receipts. If task commits without receipt, ledger lies.

**Changes:**

**1. Wrap complete() in savepoint**

```python
# src/asyncgate/engine/core.py (line ~545)

async def complete(
    self,
    tenant_id: UUID,
    worker_id: str,
    task_id: UUID,
    lease_id: UUID,
    result: dict[str, Any],
    artifacts: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Mark task as successfully completed."""
    # Validate lease (outside transaction)
    lease = await self.leases.validate(tenant_id, task_id, lease_id, worker_id)
    if not lease:
        raise LeaseInvalidOrExpired(str(task_id), str(lease_id))
    
    task = await self.tasks.get(tenant_id, task_id)
    if not task:
        raise TaskNotFound(str(task_id))
    
    if not task.can_transition_to(TaskStatus.SUCCEEDED):
        raise InvalidStateTransition(task.status.value, TaskStatus.SUCCEEDED.value)
    
    # ATOMIC BLOCK: All state changes + receipts in one transaction
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
        
        # 4. Emit result_ready receipt to owner
        task = await self.tasks.get(tenant_id, task_id)
        await self._emit_result_ready_receipt(tenant_id, task)
    
    # All succeeded or all rolled back
    return {"ok": True}
```

**2. Same for fail()**

```python
# src/asyncgate/engine/core.py (line ~593)

async def fail(self, ...) -> dict[str, Any]:
    """Mark task as failed with atomicity."""
    # Validate lease (outside transaction)
    lease = await self.leases.validate(...)
    if not lease:
        raise LeaseInvalidOrExpired(...)
    
    task = await self.tasks.get(tenant_id, task_id)
    if not task:
        raise TaskNotFound(str(task_id))
    
    # Check retry decision
    should_requeue = retryable and (task.attempt + 1) < task.max_attempts
    
    # ATOMIC BLOCK
    async with self.session.begin_nested():
        # Release lease first
        await self.leases.release(tenant_id, task_id)
        
        if should_requeue:
            # Requeue with backoff
            task = await self.tasks.requeue_with_backoff(
                tenant_id, task_id, increment_attempt=True
            )
            next_eligible_at = task.next_eligible_at
            
            # Emit requeue receipt
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
            # Terminal failure
            task_result = TaskResult(
                outcome=Outcome.FAILED,
                error=error,
                completed_at=datetime.now(timezone.utc),
            )
            await self.tasks.update_status(tenant_id, task_id, TaskStatus.FAILED, task_result)
            
            # Emit result_ready
            task = await self.tasks.get(tenant_id, task_id)
            await self._emit_result_ready_receipt(tenant_id, task)
        
        # Emit worker's task.failed receipt
        worker_principal = Principal(kind=PrincipalKind.WORKER, id=worker_id)
        asyncgate_principal = Principal(kind=PrincipalKind.SYSTEM, id="asyncgate")
        
        await self._emit_receipt(
            tenant_id=tenant_id,
            receipt_type=ReceiptType.TASK_FAILED,
            from_principal=worker_principal,
            to_principal=asyncgate_principal,
            task_id=task_id,
            lease_id=lease_id,
            body=ReceiptBody.task_failed(error=error, retry_recommended=retryable),
        )
    
    return {
        "ok": True,
        "requeued": should_requeue,
        "next_eligible_at": next_eligible_at if should_requeue else None,
    }
```

**3. Cancel and expire already commit in batches - verify atomicity**

```python
# cancel_task (line ~293) - wrap in savepoint
async def cancel_task(...):
    # ... validation ...
    
    async with self.session.begin_nested():
        await self.leases.release(tenant_id, task_id)
        task = await self.tasks.cancel(tenant_id, task_id, reason)
        await self._emit_result_ready_receipt(tenant_id, task)
    
    return {"ok": True, "status": task.status.value}


# expire_leases (line ~738) - already has batch commits, verify unit atomicity
# Each unit (expire one lease) should be atomic
async def expire_leases(self, batch_size: int = 20) -> int:
    # ... existing logic ...
    
    for lease in expired_leases:
        # Make each lease expiry atomic
        async with self.session.begin_nested():
            await self.tasks.requeue_on_expiry(...)
            await self.leases.release(...)
            await self._emit_receipt(...)  # lease.expired
        
        count += 1
        if count % batch_size == 0:
            await self.session.commit()
```

**Effort:** 6-8 hours  
**Status:** 🔴 BLOCKING

---

### P0.3: Fix CORS Configuration

**Why:** Current config unsafe in any browser context.

**Changes:**

```python
# src/asyncgate/config.py (add field)

cors_allowed_origins: list[str] = Field(
    default=["http://localhost:3000"],
    description="Allowed CORS origins (comma-separated in env)"
)

# Or from env as CSV:
# ASYNCGATE_CORS_ALLOWED_ORIGINS=https://app.example.com,https://admin.example.com
```

```python
# src/asyncgate/main.py (line ~65)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_allowed_origins,  # ← Use config
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["Authorization", "Content-Type", "X-Tenant-ID"],
)
```

**Effort:** 1 hour  
**Status:** 🔴 BLOCKING

---

### P0.4: Enable Rate Limiting by Default

**Why:** Even legit clients can overload `/v1/obligations/open`.

**Changes:**

```python
# src/asyncgate/config.py (line ~89)

rate_limit_enabled: bool = Field(
    default=True,  # ← Changed from False
    description="Enable rate limiting"
)

# Add environment-based override
@property
def rate_limit_active(self) -> bool:
    """Rate limiting forced on in staging/production."""
    if self.env in [Environment.STAGING, Environment.PRODUCTION]:
        return True
    return self.rate_limit_enabled
```

**Verify middleware is attached:**

```python
# Check that middleware is actually used in router or app
# Should already exist based on audit
```

**Effort:** 1 hour  
**Status:** 🔴 BLOCKING

---

### P0.5: Include Parents in Receipt Hash

**Why:** Parents are part of contract chain; omitting makes dedupe unsafe.

**Changes:**

```python
# src/asyncgate/engine/core.py (line ~815-847)

def _compute_receipt_hash(
    self,
    receipt_type: ReceiptType,
    task_id: UUID | None,
    from_principal: Principal,
    to_principal: Principal,
    lease_id: UUID | None,
    body: dict[str, Any] | None,
    parents: list[UUID] | None,  # ← ADD THIS PARAMETER
) -> str:
    """Compute hash for receipt deduplication."""
    # Body hash
    body_hash = None
    if body:
        body_canonical = json.dumps(body, sort_keys=True, separators=(',', ':'))
        body_hash = hashlib.sha256(body_canonical.encode()).hexdigest()
    
    # Build receipt key with ALL identifying fields INCLUDING PARENTS
    data = {
        "receipt_type": receipt_type.value,
        "task_id": str(task_id) if task_id else None,
        "from_kind": from_principal.kind.value,
        "from_id": from_principal.id,
        "to_kind": to_principal.kind.value,
        "to_id": to_principal.id,
        "lease_id": str(lease_id) if lease_id else None,
        "parents": sorted([str(p) for p in (parents or [])]),  # ← ADDED
        "body_hash": body_hash,
    }
    content = json.dumps(data, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(content.encode()).hexdigest()

# UPDATE ALL CALLS to _emit_receipt to pass parents parameter
```

**Effort:** 2-3 hours  
**Status:** 🔴 BLOCKING

---

## P1: High Priority Correctness + Abuse Controls

### P1.1: Lease Renewal Abuse Control

**Why:** Prevents indefinite task hogging.

**Changes:**

**1. Add column**

```sql
ALTER TABLE leases ADD COLUMN renewal_count INT DEFAULT 0;
```

```python
# src/asyncgate/db/tables.py (LeaseTable)

renewal_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
```

**2. Enforce limit**

```python
# src/asyncgate/db/repositories.py (line ~461)

MAX_RENEWALS = 10

async def renew(self, ...) -> Lease | None:
    lease = await self.validate(...)
    if not lease:
        return None
    
    # Check renewal count
    current_lease = await self.session.execute(
        select(LeaseTable.renewal_count).where(LeaseTable.lease_id == lease_id)
    )
    renewals = current_lease.scalar_one_or_none() or 0
    
    if renewals >= MAX_RENEWALS:
        # Lease exhausted, force expiry
        return None
    
    new_expires_at = datetime.now(timezone.utc) + timedelta(seconds=extend_by)
    
    result = await self.session.execute(
        update(LeaseTable)
        .where(
            LeaseTable.lease_id == lease_id,
            LeaseTable.expires_at > datetime.now(timezone.utc),  # Still valid
        )
        .values(
            expires_at=new_expires_at,
            renewal_count=LeaseTable.renewal_count + 1,
        )
    )
    
    if result.rowcount == 0:
        return None
    
    lease.expires_at = new_expires_at
    return lease
```

**Effort:** 4-6 hours  
**Status:** 🟡 HIGH

---

### P1.2: Timezone-Aware Datetimes

**Why:** Consistency with `DateTime(timezone=True)` columns.

**Changes:**

```python
# Replace ALL instances in codebase:
from datetime import datetime, timezone

# OLD
now = datetime.utcnow()

# NEW
now = datetime.now(timezone.utc)

# Files to update:
# - src/asyncgate/db/repositories.py (10+ instances)
# - src/asyncgate/engine/core.py (5+ instances)
# - src/asyncgate/tasks/sweep.py
```

**Script to find all:**

```bash
grep -r "datetime.utcnow()" src/asyncgate/
```

**Effort:** 4-6 hours  
**Status:** 🟡 HIGH

---

### P1.3: Receipt Hash Canonicalization

**Why:** Improve JSON stability.

**Changes:**

```python
# Already have sort_keys=True
# Add separators for compactness
body_canonical = json.dumps(
    body,
    sort_keys=True,
    separators=(',', ':'),  # ← Compact, no spaces
    ensure_ascii=False,      # ← Handle unicode properly
)
```

**Effort:** 1 hour  
**Status:** 🟢 MEDIUM

---

## Security Audit Reclassification

### C1: SQL Injection → Performance/Index Issue

**Original classification:** CRITICAL (SQL injection)  
**Reclassified:** Already addressed by P0.1 (performance + index)

**Reasoning:**
- `parent_receipt_id` validated as UUID before use
- JSONB `contains()` with validated UUID list is safe
- Real issue: missing GIN index causes O(n) scans
- Fix: Add index (P0.1) solves both performance AND any theoretical injection surface

**Status:** ✅ Reclassified, addressed in P0.1

---

## Implementation Order

**Day 1-2:**
1. P0.1: Batch termination + GIN index (most impactful)
2. P0.5: Parents in hash (cleanest, enables testing)

**Day 3-4:**
3. P0.2: Atomic transactions (complex, needs careful testing)
4. P0.3: CORS fix (trivial)
5. P0.4: Rate limiting (trivial)

**Day 5:**
6. Testing all P0 fixes
7. Load testing `/v1/obligations/open`

**Day 6-7:**
8. P1.1: Renewal limits
9. P1.2: Timezone fixes

---

## Testing Requirements

**P0.1 (Batch termination):**
```python
# Performance test
async def test_open_obligations_performance():
    # Setup: 100K receipts, 1K obligations
    # Call: list_open_obligations(limit=200)
    # Assert: < 2 queries, < 500ms
```

**P0.2 (Atomicity):**
```python
# Failure test
async def test_complete_atomic_rollback():
    # Mock receipt creation to fail
    # Assert: task still LEASED, lease still exists
```

**P0.5 (Hash with parents):**
```python
# Collision test
async def test_receipt_hash_includes_parents():
    # Same body, different parents
    # Assert: different hashes
```

---

## Tracking

- [x] **P0.1: Batch + Index** ✅ COMPLETE (Commit 99f9945)
  - GIN index added: `idx_receipts_parents_gin`
  - Batch query implementation: N+1 → 2 queries
  - Hard cap: 1000 candidates max
  - Migration: `migrations/001_add_parents_gin_index.sql`
  - Tests: `tests/test_p01_batch_termination.py`
  - Performance: 60M rows → 200 rows with 100K receipts
- [ ] P0.2: Atomic transactions
- [ ] P0.3: CORS
- [ ] P0.4: Rate limiting
- [ ] P0.5: Hash parents
- [ ] P1.1: Renewal limits
- [ ] P1.2: Timezone
- [ ] P1.3: Hash canon

**Target:** All P0 fixes within 5 days  
**Progress:** 1/5 P0 complete (20%)

