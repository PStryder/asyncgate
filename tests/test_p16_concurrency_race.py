"""
Concurrency and race condition tests.
"""

import asyncio
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from asyncgate.db.repositories import LeaseRepository, TaskRepository
from asyncgate.models import Principal, PrincipalKind


@pytest.mark.asyncio
async def test_concurrent_claim_only_one_worker(engine):
    """Two concurrent claims should lease a task only once."""
    async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    tenant_id = uuid4()
    agent = Principal(kind=PrincipalKind.AGENT, id="test-agent")

    # Create a single task
    async with async_session() as session:
        task_repo = TaskRepository(session)
        await task_repo.create(
            tenant_id=tenant_id,
            type="test_task",
            payload={"data": "test"},
            created_by=agent,
        )
        await session.commit()

    async with async_session() as session_a, async_session() as session_b:
        leases_a, leases_b = await asyncio.gather(
            LeaseRepository(session_a).claim_next(
                tenant_id=tenant_id,
                worker_id="worker-a",
                max_tasks=1,
            ),
            LeaseRepository(session_b).claim_next(
                tenant_id=tenant_id,
                worker_id="worker-b",
                max_tasks=1,
            ),
        )
        await session_a.commit()
        await session_b.commit()

    total_claimed = len(leases_a) + len(leases_b)
    assert total_claimed == 1
