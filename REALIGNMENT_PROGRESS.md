# AsyncGate Architectural Realignment Progress

**Session Started:** 2026-01-05
**Goal:** Realign AsyncGate from "attention inbox" to "obligation ledger" model per specification document

---

## COMPLETED: TIER 0 - Foundation (Build New Patterns)

### ✅ T0.1: Termination Registry (CORRECTED)
**File:** `src/asyncgate/models/termination.py` (NEW - 114 lines)

**What it does:**
- Defines termination as TYPE SEMANTICS, not ledger scanning
- Static truth table: `TERMINATION_RULES: dict[ReceiptType, set[ReceiptType]]`
- Derived set: `TERMINAL_TYPES` (union of all terminal types)
- Functions:
  - `get_terminal_types(obligation_type)` - What types CAN terminate this?
  - `is_terminal_type(receipt_type)` - Is this type capable of termination?
  - `can_terminate_type(terminal_type, obligation_type)` - Type compatibility
  - `get_obligation_types()` - All types that create obligations

**Key principle:** Separation of concerns
- Static: "What types are ALLOWED to terminate what" (this module)
- Runtime: "Did termination happen?" (database query)
- NO ledger scanning (O(n)) - keeps AsyncGate dumb and fast

### ✅ T0.2: Receipt Chain Query Primitives (EXTENDED)
**File:** `src/asyncgate/db/repositories.py` (ReceiptRepository)

**Added methods:**
- `get_by_id(tenant_id, receipt_id)` - Fetch specific receipt
- `get_by_parent(tenant_id, parent_id, limit)` - Find all child receipts
  - Uses PostgreSQL JSONB containment on parents array
- **NEW:** `has_terminator(tenant_id, parent_receipt_id) -> bool`
  - Fast O(1) EXISTS query, doesn't load data
  - DB-driven termination check
- **NEW:** `get_terminators(tenant_id, parent_receipt_id, limit)`
  - Get all receipts that terminate a parent (may include retries/duplicates)
  - Alias for get_by_parent with clearer semantics
- **NEW:** `get_latest_terminator(tenant_id, parent_receipt_id)`
  - Get most recent terminator (canonical one)
  - Simplifies agent logic when retries/duplicates exist

**Purpose:** Agents can walk receipt chains efficiently without loading full ledgers

### ✅ T0.3: Open Obligations Query (OPTIMIZED)
**Files:** `src/asyncgate/db/repositories.py` + `src/asyncgate/engine/core.py`

**Repository method:**
- `list_open_obligations(tenant_id, to_kind, to_id, since_receipt_id, limit)`
  - Queries obligation types (from TERMINATION_RULES)
  - Filters to principal
  - For each candidate, uses `has_terminator()` for O(1) check
  - Returns only obligations without terminators
  - No semantic inference, pure DB logic

**Engine methods:**
- `get_receipt(tenant_id, receipt_id)` - Single receipt lookup
- `list_receipts_by_parent(tenant_id, parent_id, limit)` - All child receipts
- **NEW:** `get_latest_terminator(tenant_id, parent_id)` - Canonical terminator
- **NEW:** `has_terminator(tenant_id, parent_id)` - Fast existence check
- `list_open_obligations(tenant_id, principal, since_receipt_id, limit)`
  - Returns: `{open_obligations: [Receipt], cursor: UUID}`
  - Pure ledger dump, no bucketing

**This IS the correct bootstrap:** Uncommitted obligations from ledger (DB-driven)

---

## NEXT: TIER 1 - Validation (Enforce Correct Patterns)

### T1.1: Enforce Parent Linkage on Terminal Receipts (PRIORITY 1)
**File:** `src/asyncgate/db/repositories.py` (ReceiptRepository.create)
**Status:** NOT STARTED

**What to do:**
1. Import `is_terminal_type` from termination module
2. In `ReceiptRepository.create()`:
   ```python
   if is_terminal_type(receipt_type):
       if not parents or len(parents) == 0:
           raise ValueError(
               f"Terminal receipt {receipt_type} must specify parents. "
               f"Without parent linkage, obligations remain open forever."
           )
       # Optionally validate parent exists
   ```

**Why critical:** Without this, terminal receipts won't discharge obligations → haunted bootstrap

### T1.2: Enforce Locatability on Success Discharge (PRIORITY 2)
**File:** `src/asyncgate/db/repositories.py` or new validation layer
**Status:** NOT STARTED

**Phase 1 (Lenient - implement first):**
```python
if receipt_type == ReceiptType.TASK_COMPLETED:
    body = body or {}
    has_artifacts = body.get('artifacts') is not None
    has_delivery_proof = body.get('delivery_proof') is not None
    
    if not (has_artifacts or has_delivery_proof):
        # Emit anomaly but allow creation
        await self._emit_system_anomaly(
            tenant_id=tenant_id,
            kind="success_without_locatability",
            details={
                "receipt_id": receipt_id,
                "task_id": task_id,
                "message": "Success receipt lacks artifacts or delivery_proof"
            }
        )
```

**Phase 2 (Strict - after migration):**
```python
if not (has_artifacts or has_delivery_proof):
    raise ValueError(
        "Success discharge must include either artifacts (store pointers) "
        "or delivery_proof (push confirmation)"
    )
```

**Why:** Prevents "SUCCEEDED (trust me bro)" from discharging obligations without making work findable

### T1.3: Add Locatability Fields to ReceiptBody (PRIORITY 2)
**File:** `src/asyncgate/models/receipt.py`
**Status:** NOT STARTED

Add `delivery_proof` parameter to `ReceiptBody.task_completed()`:
```python
@staticmethod
def task_completed(
    result_summary: str,
    result_payload: dict | None = None,
    artifacts: dict | None = None,  # Existing
    delivery_proof: dict | None = None,  # NEW
    completion_metadata: dict | None = None,
) -> dict[str, Any]:
    """
    Body for task.completed receipt.
    
    Locatability requirement: Must provide EITHER artifacts OR delivery_proof.
    - artifacts: Pointers to stored work product (e.g., S3 URLs, drive IDs)
    - delivery_proof: Evidence of push delivery (delivered_to, ack_receipt_id, timestamp)
    """
    return {
        "result_summary": result_summary,
        "result_payload": result_payload,
        "artifacts": artifacts,
        "delivery_proof": delivery_proof,  # NEW
        "completion_metadata": completion_metadata or {},
    }
```

---

## TIER 2: Bootstrap Replacement (NOT STARTED)

### T2.1: Create New Bootstrap Endpoint
**File:** `src/asyncgate/api/router.py` + schemas
**What:** Add `/v1/bootstrap/obligations` endpoint
**Returns:** `{server, relationship, open_obligations: [Receipt], cursor}`
**Uses:** `AsyncGateEngine.list_open_obligations()`

### T2.2: Mark Old Bootstrap Deprecated
**Add warning header:** `X-AsyncGate-Deprecated: "Use /v1/bootstrap/obligations"`

### T2.3: Update Engine Bootstrap Logic
**Remove bucketing:** No more `waiting_results`, `assigned_tasks`, etc.

---

## TIER 3: Cleanup (NOT STARTED)

### T3.1: Remove delivered_at Control Logic
**Make delivered_at telemetry-only**, not control plane

### T3.2: Deprecate Task-State Bootstrap
**Keep tasks for execution**, remove from bootstrap truth

### T3.3: Simplify Old Bootstrap
**Make old endpoint call new obligations endpoint** internally

---

## TIER 4: Lease/Retry Separation (NOT STARTED)

### T4.1: Split Lease Expiry from Retry Backoff
**Prevent "crash eats attempts"** by not incrementing on expiry

---

## TIER 5: Polish & Documentation (NOT STARTED)

### T5.1: Receipt Size Limits
### T5.2-T5.4: Documentation Updates

---

## KEY ARCHITECTURAL POINTS

### Current Model (WRONG):
- Bootstrap = Attention system with interpreted buckets
- delivered_at controls visibility
- Task state is source of truth for obligations

### Target Model (RIGHT):
- Bootstrap = Open obligations dump from ledger
- Receipt chains define termination (parents → terminal)
- Task state = execution tracking only

### Core Primitives Now Available:
1. ✅ Termination registry (truth table for discharge)
2. ✅ Receipt chain queries (walk parent/child relationships)
3. ✅ Open obligations query (uncommitted ledger dump)

### Footguns to Avoid:
- **Footgun A:** Terminal receipts without parents → eternal obligations
- **Footgun B:** Success without locatability → unfindable work
- **Footgun C:** Treating absence as "never started" in at-least-once
- **Footgun D:** Reintroducing bucketing in bootstrap
- **Footgun E:** Receipt bodies become chat payloads (size limits needed)

---

## HOW TO RESUME

**If continuing this session:**
Start with T1.1 (parent linkage enforcement) - highest priority

**If starting fresh session:**
1. Read this file completely
2. Review `src/asyncgate/models/termination.py` to understand termination logic
3. Check `list_open_obligations()` in repositories.py and engine/core.py
4. Start with Tier 1 enforcement (parent linkage, locatability)

**Testing the foundation:**
```python
# Can query open obligations
obligations = await engine.list_open_obligations(
    tenant_id=tenant,
    principal=Principal(kind="agent", id="test"),
    limit=50
)
# Returns: {open_obligations: [Receipt], cursor: UUID}

# Can walk receipt chains
children = await engine.list_receipts_by_parent(
    tenant_id=tenant,
    parent_receipt_id=obligation_id,
    limit=10
)
# Returns: [Receipt] that reference parent
```

---

## COMMIT STATUS

**Files changed (Tier 0):**
- `src/asyncgate/models/termination.py` (NEW)
- `src/asyncgate/db/repositories.py` (MODIFIED - added 3 methods)
- `src/asyncgate/engine/core.py` (MODIFIED - added 3 methods)

**Ready to commit:** Yes, Tier 0 foundation is complete and compiles cleanly

**Commit message template:**
```
Tier 0: Foundation for obligation ledger model

Add termination registry and open obligations query primitives.
This establishes the correct model: obligations persist until
explicitly terminated by receipt chain evidence.

New modules:
- termination.py: Truth table for obligation discharge
- Receipt chain queries: get_by_id, get_by_parent
- Open obligations query: THE bootstrap primitive

No breaking changes - adds alongside existing code.
Enables Tier 1 enforcement (parent linkage, locatability).
```

---

## CONTEXT WINDOW USAGE
- Current: ~133k / 190k tokens (70%)
- Safe to continue through Tier 1
- Recommend commit after Tier 1, fresh session for Tier 2+
