"""API dependencies."""

import logging
from typing import AsyncGenerator
from uuid import UUID

from fastapi import Depends, Header, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from asyncgate.config import Environment, settings
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
    if settings.allow_insecure_dev and settings.env == Environment.DEVELOPMENT:
        return UUID("00000000-0000-0000-0000-000000000000")

    raise HTTPException(status_code=401, detail="Missing tenant ID")


async def verify_api_key(
    authorization: str | None = Header(None),
) -> bool:
    """
    Verify API key authentication.

    In v0, we use simple shared token.
    In production, this would validate JWTs or API keys.
    
    Security: Fails closed - if api_key is not configured and we're not
    in explicit insecure dev mode, all requests are rejected.
    """
    # Insecure dev mode bypass (must be explicitly enabled)
    # Note: Startup warning is logged by validate_auth_config()
    if settings.allow_insecure_dev and settings.env == Environment.DEVELOPMENT:
        return True

    # SECURITY: Fail closed if api_key not configured
    if not settings.api_key:
        logger.error(
            "SECURITY VIOLATION: api_key not configured and insecure mode disabled. "
            "Set ASYNCGATE_API_KEY or enable ASYNCGATE_ALLOW_INSECURE_DEV=true (dev only)."
        )
        raise HTTPException(
            status_code=503,
            detail="Server misconfigured: authentication not properly initialized",
        )

    if not authorization:
        raise HTTPException(status_code=401, detail="Missing authorization header")

    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Invalid authorization format")

    token = authorization[7:]
    
    import secrets
    if not secrets.compare_digest(token, settings.api_key):
        raise HTTPException(status_code=401, detail="Invalid API key")

    return True


def validate_auth_config() -> None:
    """
    Validate authentication configuration at startup.
    
    Ensures server cannot start with insecure configuration in non-dev environments.
    This prevents the footgun where missing api_key accidentally runs open.
    
    Raises:
        RuntimeError: If configuration is insecure for the current environment
    """
    # Insecure dev mode is only allowed in development
    if settings.allow_insecure_dev and settings.env != Environment.DEVELOPMENT:
        raise RuntimeError(
            f"SECURITY ERROR: allow_insecure_dev=true is only permitted in development. "
            f"Current environment: {settings.env.value}. "
            f"Set ASYNCGATE_ALLOW_INSECURE_DEV=false for {settings.env.value}."
        )
    
    # If not in insecure dev mode, api_key must be configured
    if not settings.allow_insecure_dev and not settings.api_key:
        raise RuntimeError(
            f"SECURITY ERROR: api_key is required in {settings.env.value} environment. "
            f"Set ASYNCGATE_API_KEY to a secure token. "
            f"For local development only, you can set ASYNCGATE_ALLOW_INSECURE_DEV=true."
        )
    
    # Log security status
    if settings.allow_insecure_dev:
        logger.warning(
            "=" * 80 + "\n"
            "WARNING: Running in INSECURE DEV MODE\n"
            "  - Authentication is DISABLED\n"
            "  - All API requests will be accepted without verification\n"
            "  - This mode is ONLY for local development\n"
            "  - Set ASYNCGATE_ALLOW_INSECURE_DEV=false for any deployment\n"
            + "=" * 80
        )
    elif settings.api_key:
        logger.info(
            f"Authentication enabled: API key configured for {settings.env.value} environment"
        )
