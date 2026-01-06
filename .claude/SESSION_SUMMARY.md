# AsyncGate Architectural Realignment - Session Summary
**Date:** 2026-01-05
**Session ID:** asyncgate-realignment-tier0-tier1
**Commits:** 0fbdb51, 46985f7, 2df189c
**Status:** Tier 0 and Tier 1 COMPLETE

---

## CONTEXT FOR NEXT SESSION

**Repository:** F:/HexyLab/AsyncGate (github.com/PStryder/asyncgate)
**Branch:** main (synchronized)
**Latest commit:** 2df189c

**Key documentation files:**
- `REALIGNMENT_PROGRESS.md` - Comprehensive TODO list with status
- `HEXY_REFINEMENTS.md` - Critical corrections from Hexy
- `T0_CORRECTIONS.md` - Performance fixes (type semantics vs ledger scanning)

**Where we are:** Tier 1 validation complete, ready for Tier 2 (bootstrap replacement)

---

## WHAT WAS ACCOMPLISHED

### Tier 0: Foundation (Commits 0fbdb51, 46985f7)

**Problem solved:** Original implementation was doing O(n) ledger scanning and semantic inference

**Solution implemented:**
1. **Termination Registry** (`src/asyncgate/models/termination.py`)
   - Pure type semantics (static truth table)
   - `TERMINATION_RULES: dict[ReceiptType, set[ReceiptType]]`
   - No database access, no runtime logic
   - O(1) type checking

2. **DB-Driven Termination Checks** (`src/asyncgate/db/repositories.py`)
   - `has_terminator(parent_receipt_id) -> bool` (O(1) EXISTS)
   - `get_terminators(parent_receipt_id, limit)` (all terminators)
   - `get_latest_terminator(parent_receipt_id)` (canonical, most recent)
   - Separation: types define "what CAN", DB answers "DID it"

3. **Receipt Chain Queries**
   - `get_by_id()`, `get_by_parent()` for provenance walking
   - `list_open_obligations()` - THE bootstrap primitive
   - Pure ledger dump, no bucketing, no interpretation

**Key insight:** AsyncGate stays dumb and fast - no semantic inference

### Tier 1: Validation (Commit 2df189c)

**Footguns eliminated:**

**A. Terminal receipts without parents → eternal obligations**
- Solution: ValueError on creation
- Validates parent exists, tenant matches
- Does NOT validate principal (different actors discharge obligations)

**B. Success without locatability → unfindable work**
- Solution: Strip parents, keep obligation open
- Phase 1 (lenient): Allow creation but obligation NOT discharged
- Phase 2 (future): Reject entirely

**Implementation:**
1. **Parent Linkage Enforcement** (`src/asyncgate/db/repositories.py`)
   ```python
   if is_terminal_type(receipt_type):
       if not parents:
           raise ValueError("must specify parents")
   ```

2. **Locatability Enforcement** (`src/asyncgate/db/repositories.py`)
   ```python
   if TASK_COMPLETED and not (artifacts or delivery_proof):
       parents_to_use = []  # Strip! Obligation stays open
       log.warning("SUCCESS WITHOUT LOCATABILITY")
   ```

3. **Unopinionated delivery_proof** (`src/asyncgate/models/receipt.py`)
   ```python
   delivery_proof = {
       "mode": "push" | "store",
       "target": {...},
       "status": "succeeded" | "failed",
       "at": "timestamp",
       "proof": {...}  # request_id, etag, http_status, etc.
   }
   ```

---

## HEXY'S CRITICAL REFINEMENTS

**1. No Principal Matching in Parent Validation**
- Different actors discharge obligations (worker → agent, AsyncGate → worker)
- "That's a feature, not a bug"
- Only validate: tenant matches, parent exists, type legal

**2. Success Without Locatability → Obligation Stays Open**
- "Do NOT treat the parent obligation as terminated (important!)"
- Strip parents, log anomaly, keep obligation open
- Forces producer to fix and resend properly

**3. Unopinionated delivery_proof Shape**
- Original was "too agent-ish"
- New shape supports both push AND store realities
- Mode + target + status + proof fields

**4. Lease Expiry ≠ Task Failure**
- "Worker crash shouldn't eat attempts"
- Split requeue_on_expiry (no attempt++) from retry backoff
- Tier 4 priority

**5. Keep delivered_at as Telemetry**
- Don't rush removal
- Useful observability: "Did agent retrieve obligations?"
- Just never use for control logic

**6. Test "Unbucketed Bootstrap" Property**
- Prevent regression to inbox model
- Assert response has ONLY: obligations list + cursor
- No helpful categorization

---

## ARCHITECTURAL MODEL

**OLD (WRONG):**
```
Bootstrap = Attention system with interpreted buckets
delivered_at = controls visibility
Task state = source of truth for obligations
```

**NEW (CORRECT):**
```
Bootstrap = Open obligations dump from ledger
Receipt chains = define termination (parents → terminal)
Task state = execution tracking ONLY
```

**Core principle:** Termination is TYPE SEMANTICS + DB EVIDENCE
- Static: "What types CAN terminate what" (termination.py)
- Runtime: "Did termination happen?" (database EXISTS query)
- AsyncGate stays dumb - no semantic inference

---

## WHAT'S NEXT (Tier 2)

**Bootstrap Replacement** - Create new endpoint, deprecate old

**File:** `src/asyncgate/api/router.py` + schemas

**Endpoint naming options:**
- `/v1/obligations/open` (reflects model directly)
- `/v1/obligations` (simpler)
- `/v1/bootstrap/obligations` (parallel to existing)

**Returns:** `{server, relationship, open_obligations: [Receipt], cursor}`

**Removes:**
- `waiting_results` (bucketing)
- `assigned_tasks` (task state)
- `running_or_scheduled` (mixed concerns)
- All delivered_at filtering

**Key test:** Assert response is unbucketed (obligations + cursor only)

---

## TIER 3-6 REMAINING WORK

**Tier 3: Cleanup**
- Keep delivered_at as telemetry only
- Remove task-state bootstrap logic
- Simplify old bootstrap endpoint

**Tier 4: Lease/Retry Separation** (CRITICAL)
- Add requeue_on_expiry() (no attempt increment)
- Keep requeue_with_backoff() for actual failures
- "Lost authority" vs "task failed"

**Tier 5: Polish**
- Receipt size limits (64KB body, 10 parents, 100 artifacts)
- Error message: "Receipts are contracts, not chat"
- Documentation updates

**Tier 6: Testing**
- Termination logic tests
- Parent linkage tests
- Locatability tests
- **Unbucketed bootstrap test** (anti-regression)

---

## KEY QUOTES

> "Termination is TYPE SEMANTICS + DB EVIDENCE, not ledger scanning"

> "AsyncGate stays dumb and fast - no semantic inference"

> "Receipts are contracts, not chat"

> "Lease expiry is 'lost authority,' not 'task failed'"

> "Different actors discharge obligations - that's a feature, not a bug"

> "Do NOT treat the parent obligation as terminated" [without locatability]

---

## FILES TO REVIEW FOR PICKUP

**Progress tracking:**
- `REALIGNMENT_PROGRESS.md` (comprehensive TODO with status)
- `HEXY_REFINEMENTS.md` (all corrections documented)
- `T0_CORRECTIONS.md` (performance fixes explained)

**Code changes:**
- `src/asyncgate/models/termination.py` (type semantics)
- `src/asyncgate/db/repositories.py` (DB checks + validation)
- `src/asyncgate/engine/core.py` (query primitives)
- `src/asyncgate/models/receipt.py` (locatability fields)

**Commit history:**
```
2df189c - Tier 1: Validation (parent linkage + locatability)
46985f7 - Tier 0 CORRECTIONS (type semantics + DB-driven)
0fbdb51 - Tier 0: Foundation (obligation ledger model)
e2d668a - P0: Instance ID uniqueness (auto-detection)
b26acf6 - P1: Auth footgun fix (fail closed)
```

---

## IMPLEMENTATION NOTES

**What compiles cleanly:**
- All Tier 0 foundation code
- All Tier 1 validation code
- No breaking changes to existing deployments

**What's tested:**
- Manual compilation checks (py_compile)
- No unit tests yet (Tier 6)

**What's NOT done:**
- system.anomaly receipt emission (TODO in locatability check)
- Strict locatability enforcement (Phase 2)
- New bootstrap endpoint
- All Tier 3-6 work

---

## HANDOFF CHECKLIST

If picking up in fresh session:

1. ✅ Read `REALIGNMENT_PROGRESS.md` (shows what's done/remaining)
2. ✅ Read `HEXY_REFINEMENTS.md` (critical corrections)
3. ✅ Review Tier 2 section in progress doc
4. ✅ Start with `/v1/obligations/open` endpoint creation
5. ✅ Wire to `AsyncGateEngine.list_open_obligations()`
6. ✅ Add deprecation warning to old `/v1/bootstrap`
7. ✅ Test unbucketed response (no bucketing!)

**Context from this session:** ~113k tokens used, clean slate recommended for Tier 2

**Estimated work:**
- Tier 2: ~1 hour (endpoint + deprecation)
- Tier 4: ~45 min (lease/retry split)
- Tier 5-6: ~1-2 hours (polish + tests)

---

## CONTACT POINTS

**Repository:** github.com/PStryder/asyncgate
**Documentation:** All in `/docs` and root-level `.md` files
**Specification:** Original punch list in session context (see MemoryGate)

**For questions:** All design decisions documented in:
- `HEXY_REFINEMENTS.md` (corrections from Hexy)
- `T0_CORRECTIONS.md` (performance rationale)
- Commit messages (detailed explanations)
