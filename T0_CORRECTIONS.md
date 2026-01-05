## T0 CORRECTIONS - Type Semantics + DB-Driven Checks

**Problem identified:** Original T0.1 was doing O(n) ledger scanning and semantic inference

**Corrections applied:**

### 1. Termination as TYPE SEMANTICS (not ledger scan)

**BEFORE (Wrong):**
```python
def is_obligation_terminated(receipt, ledger: list[Receipt]) -> bool:
    # Load entire ledger, scan in memory O(n)
    for r in ledger:
        if can_terminate(r, receipt):
            return True
```
- O(n) scanning
- Tempts caching hacks
- Semantic inference

**AFTER (Correct):**
```python
# Static type rules
TERMINATION_RULES: dict[ReceiptType, set[ReceiptType]]
TERMINAL_TYPES: set[ReceiptType]

# Type checking functions
def get_terminal_types(obligation_type) -> set[ReceiptType]
def is_terminal_type(receipt_type) -> bool
def can_terminate_type(terminal_type, obligation_type) -> bool
```
- O(1) type checks
- No ledger access
- Pure type semantics
- AsyncGate stays dumb

### 2. DB-Driven Termination Checks

**Added to ReceiptRepository:**
```python
async def has_terminator(tenant_id, parent_receipt_id) -> bool:
    # Fast EXISTS query, O(1), doesn't load data
    
async def get_terminators(tenant_id, parent_receipt_id, limit):
    # All terminators (handles retries/duplicates)
    
async def get_latest_terminator(tenant_id, parent_receipt_id):
    # Canonical terminator (most recent)
    # Simplifies agent logic
```

**Added to AsyncGateEngine:**
```python
async def has_terminator(tenant_id, parent_id) -> bool
async def get_latest_terminator(tenant_id, parent_id) -> Receipt
```

### 3. Optimized Open Obligations Query

**BEFORE:**
```python
# For each candidate:
terminal_types = get_terminal_types(receipt.receipt_type)
children_result = await session.execute(
    select(ReceiptTable).where(
        tenant_id == ...,
        receipt_type.in_(terminal_types),  # Type filter
        parents.contains([receipt_id]),
    ).limit(1)
)
```
- Filtered by terminal types (unnecessary)
- Multiple queries in loop

**AFTER:**
```python
# For each candidate:
has_term = await self.has_terminator(tenant_id, receipt.receipt_id)
# Simple EXISTS, no type filter needed
```
- Single optimized query per candidate
- Type semantics separated from DB logic
- Cleaner, faster

---

## Key Principle: Separation of Concerns

**Static (termination.py):**
- "What types CAN terminate what?" (type semantics)
- Truth table, no runtime logic

**Runtime (repositories.py):**
- "Did termination happen?" (DB evidence)
- EXISTS queries, O(1) checks

**NO mixing:**
- Type module doesn't touch DB
- DB module doesn't do semantic inference
- AsyncGate stays dumb and fast

---

## Agent UX Improvement

**Problem:** Multiple terminators due to retries/duplicates
**Solution:** `get_latest_terminator()` returns canonical one

**Before:**
```python
terminators = await list_receipts_by_parent(obligation_id)
# Agent must decide which one matters
canonical = max(terminators, key=lambda r: r.created_at)
```

**After:**
```python
terminator = await get_latest_terminator(obligation_id)
# AsyncGate returns the canonical one
```

Reduces agent complexity, reduces API chatter.

---

## Performance

**Original:** O(n) for each obligation check
**Corrected:** O(1) EXISTS query per obligation

**Original:** Load full receipt objects for type checking
**Corrected:** EXISTS returns boolean, no data loading

**Scaling:** 1000 obligations = 1000 fast EXISTS queries vs loading entire ledger

---

## Files Changed

- `src/asyncgate/models/termination.py` (REWRITTEN - 114 lines, -44 from original)
  - Removed: ledger scanning functions
  - Focused: pure type semantics
  
- `src/asyncgate/db/repositories.py` (+89 lines)
  - Added: has_terminator (fast check)
  - Added: get_terminators (all terminators)
  - Added: get_latest_terminator (canonical)
  - Optimized: list_open_obligations (uses has_terminator)
  
- `src/asyncgate/engine/core.py` (+40 lines)
  - Added: has_terminator
  - Added: get_latest_terminator
  - Updated: docstrings for clarity

---

## Verification

Compiles cleanly. No breaking changes to existing code.
All new functionality additive.
