"""AsyncGate main application."""

import asyncio
import logging
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from asyncgate.api import router
from asyncgate.api.deps import validate_auth_config
from asyncgate.config import settings
from asyncgate.db.base import close_db, init_db
from asyncgate.tasks.sweep import start_lease_sweep, stop_lease_sweep

# Configure logging
logging.basicConfig(
    level=getattr(logging, settings.log_level.upper()),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("asyncgate")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager."""
    logger.info("Starting AsyncGate server...")
    logger.info(f"Instance ID: {settings.instance_id}")
    logger.info(f"Environment: {settings.env.value}")
    logger.info(f"Receipt mode: {settings.receipt_mode.value}")

    # Validate authentication configuration (fail fast if insecure)
    validate_auth_config()

    # Initialize database
    await init_db()
    logger.info("Database initialized")

    # Start background tasks
    await start_lease_sweep()
    logger.info("Lease sweep task started")

    yield

    # Cleanup
    logger.info("Shutting down AsyncGate server...")
    await stop_lease_sweep()
    await close_db()
    logger.info("Shutdown complete")


# Create FastAPI application
app = FastAPI(
    title="AsyncGate",
    description="Durable, lease-based asynchronous task execution MCP server",
    version="0.1.0",
    lifespan=lifespan,
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Configure appropriately for production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include API router
app.include_router(router)


def main():
    """Entry point for the application."""
    uvicorn.run(
        "asyncgate.main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.debug,
        log_level=settings.log_level.lower(),
    )


if __name__ == "__main__":
    main()
