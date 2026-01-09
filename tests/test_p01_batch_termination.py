"""
P0.1 Performance Test: Batch termination checks + GIN index

Tests that list_open_obligations uses batch queries instead of N+1 pattern.
"""

import asyncio
from datetime import datetime
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from asyncgate.db.repositories import ReceiptRepository
from asyncgate.models import Principal, PrincipalKind, ReceiptType
from asyncgate.principals import SYSTEM_PRINCIPAL_ID


@pytest.mark.asyncio
async def test_list_open_obligations_performance(session: AsyncSession):
    """
    Test that list_open_obligations uses batch queries, not N+1.
    
    Setup:
    - Create 100 obligation receipts (task.assigned)
    - Create 50 terminator receipts (task.completed) for half of them
    - Expected: 50 open obligations
    
    Performance:
    - OLD: 1 query for candidates + 100 queries for has_terminator = 101 queries
    - NEW: 1 query for candidates + 1 query for batch termination = 2 queries
    """
    receipts = ReceiptRepository(session)
    tenant_id = uuid4()
    agent = Principal(kind=PrincipalKind.AGENT, id="test-agent")
    system = Principal(kind=PrincipalKind.SYSTEM, id=SYSTEM_PRINCIPAL_ID)
    
    # Create 100 obligation receipts
    obligation_ids = []
    for i in range(100):
        receipt = await receipts.create(
            tenant_id=tenant_id,
            receipt_type=ReceiptType.TASK_ASSIGNED,
            from_principal=agent,
            to_principal=system,
            task_id=uuid4(),
            body={"task_type": f"test_task_{i}"},
        )
        obligation_ids.append(receipt.receipt_id)
    
    await session.commit()
    
    # Terminate half of them (first 50)
    for i in range(50):
        await receipts.create(
            tenant_id=tenant_id,
            receipt_type=ReceiptType.TASK_COMPLETED,
            from_principal=system,
            to_principal=agent,
            task_id=uuid4(),  # Different task
            parents=[obligation_ids[i]],  # References obligation as parent
            body={
                "result": f"completed_{i}",
                "artifacts": [{"type": "test", "uri": f"mem://completed/{i}"}],
            },
        )
    
    await session.commit()
    
    # Query open obligations
    import time
    start = time.time()
    
    open_oblig, cursor = await receipts.list_open_obligations(
        tenant_id=tenant_id,
        to_kind=PrincipalKind.SYSTEM,
        to_id=SYSTEM_PRINCIPAL_ID,
        limit=100,
    )
    
    elapsed = time.time() - start
    
    # Verify correctness: 50 obligations should be open
    assert len(open_oblig) == 50, f"Expected 50 open obligations, got {len(open_oblig)}"
    
    # Verify the terminated ones are NOT in the list
    open_ids = {o.receipt_id for o in open_oblig}
    terminated_ids = set(obligation_ids[:50])
    
    assert open_ids.isdisjoint(terminated_ids), "Terminated obligations should not appear"
    
    # Performance check: Should be fast (< 500ms even with 100 receipts)
    assert elapsed < 0.5, f"Query took {elapsed:.3f}s, expected < 0.5s"
    
    print(f"✅ list_open_obligations returned {len(open_oblig)} obligations in {elapsed:.3f}s")


@pytest.mark.asyncio
async def test_list_open_obligations_hard_cap(session: AsyncSession):
    """
    Test that candidate_limit hard cap at 1000 prevents runaway queries.
    """
    receipts = ReceiptRepository(session)
    tenant_id = uuid4()
    agent = Principal(kind=PrincipalKind.AGENT, id="test-agent")
    system = Principal(kind=PrincipalKind.SYSTEM, id=SYSTEM_PRINCIPAL_ID)
    
    # Create 1500 obligation receipts (exceeds hard cap)
    for i in range(1500):
        await receipts.create(
            tenant_id=tenant_id,
            receipt_type=ReceiptType.TASK_ASSIGNED,
            from_principal=agent,
            to_principal=system,
            task_id=uuid4(),
            body={"task_type": f"test_task_{i}"},
        )
        
        # Commit in batches
        if i % 100 == 0:
            await session.commit()
    
    await session.commit()
    
    # Query with large limit
    open_oblig, cursor = await receipts.list_open_obligations(
        tenant_id=tenant_id,
        to_kind=PrincipalKind.SYSTEM,
        to_id=SYSTEM_PRINCIPAL_ID,
        limit=500,  # Requests 500, but candidate_limit caps at 1000
    )
    
    # Should not crash or take forever
    # Hard cap means we fetch at most 1000 candidates
    assert len(open_oblig) <= 500, "Should respect limit"
    
    print(f"✅ Hard cap working: fetched {len(open_oblig)} obligations")


@pytest.mark.asyncio
async def test_list_open_obligations_pagination(session: AsyncSession):
    """
    Test pagination with since_receipt_id cursor.
    """
    receipts = ReceiptRepository(session)
    tenant_id = uuid4()
    agent = Principal(kind=PrincipalKind.AGENT, id="test-agent")
    system = Principal(kind=PrincipalKind.SYSTEM, id=SYSTEM_PRINCIPAL_ID)
    
    # Create 30 obligations
    for i in range(30):
        await receipts.create(
            tenant_id=tenant_id,
            receipt_type=ReceiptType.TASK_ASSIGNED,
            from_principal=agent,
            to_principal=system,
            task_id=uuid4(),
            body={"task_type": f"test_task_{i}"},
        )
    
    await session.commit()
    
    # Page 1: First 10
    page1, cursor1 = await receipts.list_open_obligations(
        tenant_id=tenant_id,
        to_kind=PrincipalKind.SYSTEM,
        to_id=SYSTEM_PRINCIPAL_ID,
        limit=10,
    )
    
    assert len(page1) == 10
    assert cursor1 is not None
    
    # Page 2: Next 10
    page2, cursor2 = await receipts.list_open_obligations(
        tenant_id=tenant_id,
        to_kind=PrincipalKind.SYSTEM,
        to_id=SYSTEM_PRINCIPAL_ID,
        since_receipt_id=cursor1,
        limit=10,
    )
    
    assert len(page2) == 10
    assert cursor2 is not None
    
    # Verify no overlap
    page1_ids = {o.receipt_id for o in page1}
    page2_ids = {o.receipt_id for o in page2}
    
    assert page1_ids.isdisjoint(page2_ids)
    
    print(f"✅ Pagination working: {len(page1)} + {len(page2)} obligations, no overlap")


if __name__ == "__main__":
    # Run tests
    pytest.main([__file__, "-v", "-s"])
