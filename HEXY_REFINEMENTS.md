# Hexy's Refinements to AsyncGate Realignment Plan

**Source:** Notes from Hexy about the punch list
**Date:** 2026-01-05

---

## T1 Validation - Corrections

### T1.1 Parent Linkage ✅ Strong, with one correction

**DO enforce:** Terminal receipts MUST specify parents
**DON'T enforce:** "parent addressed to same principal"

**Why remove principal matching:**
Different actors discharge obligations:
- Worker discharges agent's `task.assigned` obligation
- AsyncGate discharges worker's `lease` obligation  
- Scheduler discharges some other contract

**What matters:**
- ✅ Tenant matches
- ✅ Parent exists
- ✅ Terminator type is legal for that parent type
- ❌ Principal doesn't need to match

### T1.2/T1.3 Locatability ✅ Required, but refine the shape

**Problem with original proposal:**
```json
"delivery_proof": {
  "delivered_to": "principal",
  "delivered_at": "timestamp",
  "ack_receipt_id": "uuid"
}
```
Too "agent-ish", not enough for push/store realities.

**Better minimum shape (unopinionated):**
```json
"delivery_proof": {
  "mode": "push" | "store",
  "target": {...},  // endpoint spec or pointer
  "status": "succeeded" | "failed",
  "at": "timestamp",
  "proof": {...}  // request_id, etag, row_id, http_status
}
```

**Keep `artifacts` as store-pointer list:**
- S3 url, file path, db row, blob id, etc.

**Enforcement rule (lenient first):**
If receipt is success terminal AND has neither `artifacts` nor `delivery_proof`:
1. Emit `system.anomaly: success_without_locatability`
2. **CRITICAL:** Do NOT treat the parent obligation as terminated

**Strict later:** Reject the receipt entirely

**Why this matches model:** "Ledger-only receipts, product elsewhere"

---

## T2 Bootstrap Replacement ✅ Good plan, naming suggestion

**Your `/v1/bootstrap/obligations` is perfect for parallel migration**

**Naming suggestion (reflects model):**
- Option A: `/v1/obligations/open` 
- Option B: `/v1/obligations`
- Keep `/v1/bootstrap` as "relationship metadata + open obligations" if handshake packet desired

Naming isn't critical; semantics are.

---

## T3 Cleanup ✅ Don't rush it

**Correct:** Removing `delivered_at` control logic once all callers migrated

**But:** Keep `delivered_at` as telemetry
- Useful to know: "Did this agent ever successfully retrieve its obligations?"
- Just never use it for truth

---

## T4 Lease vs Retry ✅ YES, this is a real footgun today

**Confirmed critical:** Splitting lease expiry requeue from retryable failure backoff

**Why:**
- Lease expiry = "lost authority", NOT "task failed"
- Burning `attempt` on lease expiry causes false terminal failure under flaky workers

**Proposed `requeue_on_expiry()` is exactly right:**
- No attempt++
- Small jitter only
- Status back to QUEUED

---

## T5 Size Limits ✅ Make the error message a weapon

**Yes, put limit in now.** Receipts are contracts, not chat.

**Also cap:**
- `parents` length (prevent mega-chains)
- `artifacts` count (prevent stuffing)

**Error message:** "Receipt bodies are contracts, not chat messages"

---

## T6 Testing ✅ Add one more test class

**New test: "Bootstrap is unbucketed" property**

**What to test:**
Assert `/v1/obligations/open` returns ONLY:
- List of open obligations
- Cursor

**No:**
- `waiting_results`
- Task lists
- Delivery flags
- "Helpful" categorization

**Why:** Prevents regression to inbox model

---

## Key Insights from Hexy

### 1. Different Actors Discharge Obligations
This is NOT a bug, it's a feature:
- Agent creates task.assigned obligation
- Worker discharges it with task.completed
- Principals don't need to match

### 2. Locatability Must Be Unopinionated
Push and store are fundamentally different:
- Push: request_id, http_status, ack receipt
- Store: s3 url, db row id, etag

Don't force agent-style shape on all patterns.

### 3. Success Without Locatability Doesn't Discharge
This is CRITICAL enforcement detail:
- Emit anomaly (logging/metrics)
- BUT obligation stays open
- Forces producer to fix and resend with locatability

### 4. delivered_at Is Useful Telemetry
Don't delete it, just don't use it for control logic.
Knowing "agent retrieved obligations" is valuable observability.

### 5. Lease Expiry ≠ Task Failure
Worker crash or network partition shouldn't burn attempts.
Keep execution attempts separate from infrastructure issues.

### 6. Guard Against Model Drift
Test the "unbucketed" property explicitly.
Prevent helpful features from turning AsyncGate back into inbox.

---

## Implementation Priority

**Immediate (Tier 1):**
1. Parent linkage (without principal matching)
2. Locatability enforcement (lenient with no-discharge)
3. Unopinionated delivery_proof shape

**Next (Tier 2):**
4. Obligations endpoint (consider naming)
5. Deprecate old bootstrap

**Later (Tier 3-4):**
6. Keep delivered_at as telemetry
7. Split lease/retry logic

**Polish (Tier 5-6):**
8. Size limits with good error messages
9. Unbucketed bootstrap test

---

## Quotes Worth Preserving

> "Receipts are contracts, not chat."

> "Lease expiry is 'lost authority,' not 'task failed.'"

> "Do NOT treat the parent obligation as terminated" [when locatability missing]

> "AsyncGate stays dumb and fast - no semantic inference."

> "Different actors discharge obligations - that's a feature, not a bug."
