"""API dependencies."""

import logging
from typing import AsyncGenerator
from uuid import UUID

from fastapi import Depends, Header, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from asyncgate.config import settings
from asyncgate.db.base import async_session_factory


logger = logging.getLogger("asyncgate.api")


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """Get database session."""
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def get_tenant_id(
    x_tenant_id: str | None = Header(None, alias="X-Tenant-ID"),
) -> UUID:
    """
    Extract tenant ID from request.

    In v0, we use a header for simplicity.
    In production, this would be extracted from JWT claims.
    """
    if x_tenant_id:
        try:
            return UUID(x_tenant_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid tenant ID format")

    # Default tenant for development
    if settings.allow_insecure_dev and settings.env.value == "development":
        return UUID("00000000-0000-0000-0000-000000000000")

    raise HTTPException(status_code=401, detail="Missing tenant ID")


async def verify_api_key(
    authorization: str | None = Header(None),
) -> bool:
    """
    Verify API key authentication.

    In v0, we use simple shared token.
    In production, this would validate JWTs or API keys.
    """
    if settings.allow_insecure_dev and settings.env.value == "development":
        logger.warning(
            "INSECURE MODE ENABLED: API authentication is disabled. "
            "Set ASYNCGATE_ALLOW_INSECURE_DEV=false for production."
        )
        return True

    if not settings.api_key:
        return True

    if not authorization:
        raise HTTPException(status_code=401, detail="Missing authorization header")

    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Invalid authorization format")

    token = authorization[7:]
    if token != settings.api_key:
        raise HTTPException(status_code=401, detail="Invalid API key")

    return True
