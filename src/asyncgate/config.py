"""AsyncGate configuration management."""

from enum import Enum
from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Environment(str, Enum):
    """Application environment."""

    DEVELOPMENT = "development"
    STAGING = "staging"
    PRODUCTION = "production"


class ReceiptMode(str, Enum):
    """Receipt storage mode."""

    STANDALONE = "standalone"
    MEMORYGATE_INTEGRATED = "memorygate_integrated"


class Settings(BaseSettings):
    """AsyncGate configuration settings."""

    model_config = SettingsConfigDict(
        env_prefix="ASYNCGATE_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # General
    env: Environment = Environment.DEVELOPMENT
    instance_id: str = Field(
        default="asyncgate-1",
        description="Unique instance identifier (auto-detected at startup if not set)",
    )
    log_level: str = "INFO"
    debug: bool = False

    # Database
    database_url: str = "postgresql+asyncpg://asyncgate:asyncgate@localhost:5432/asyncgate"

    # Redis (for rate limiting)
    redis_url: Optional[str] = None

    # Receipt mode
    receipt_mode: ReceiptMode = ReceiptMode.STANDALONE

    # MemoryGate integration (only used if receipt_mode = MEMORYGATE_INTEGRATED)
    memorygate_url: Optional[str] = None
    memorygate_token: Optional[str] = None
    memorygate_tenant_id: Optional[str] = None
    memorygate_emission_timeout_ms: int = 500
    memorygate_emission_buffer_size: int = 10000
    memorygate_emission_retry_interval_seconds: int = 30
    memorygate_emission_max_retries: int = 10

    # MemoryGate circuit breaker
    memorygate_circuit_breaker_enabled: bool = Field(
        default=True, description="Enable circuit breaker for MemoryGate calls"
    )
    memorygate_circuit_breaker_failure_threshold: int = Field(
        default=5, description="Failures before opening circuit"
    )
    memorygate_circuit_breaker_timeout_seconds: int = Field(
        default=60, description="Seconds before attempting half-open"
    )
    memorygate_circuit_breaker_half_open_max_calls: int = Field(
        default=3, description="Test calls in half-open state"
    )
    memorygate_circuit_breaker_success_threshold: int = Field(
        default=2, description="Successes to close from half-open"
    )

    # Rate limiting (P0.4 - enabled by default)
    rate_limit_enabled: bool = Field(default=True, description="Enable rate limiting")
    rate_limit_backend: str = Field(
        default="memory", description="Rate limit backend: memory or redis"
    )
    rate_limit_default_calls: int = Field(
        default=100, description="Default calls per window"
    )
    rate_limit_default_window_seconds: int = Field(
        default=60, description="Default window size in seconds"
    )
    
    @property
    def rate_limit_active(self) -> bool:
        """Rate limiting is forced on in staging/production regardless of config."""
        if self.env in [Environment.STAGING, Environment.PRODUCTION]:
            return True
        return self.rate_limit_enabled

    # Lease behavior
    default_lease_ttl_seconds: int = Field(default=120, description="Default lease TTL (2 min)")
    max_lease_ttl_seconds: int = Field(default=1800, description="Max lease TTL (30 min)")
    lease_sweep_interval_seconds: int = Field(default=5, description="Lease sweep cadence")
    lease_grace_seconds: int = Field(default=0, description="Lease grace period")
    
    # P1.1: Lease renewal limits (prevents hoarding DoS)
    max_lease_renewals: int = Field(
        default=10, 
        description="Maximum times a lease can be renewed before forcing release"
    )
    max_lease_lifetime_seconds: int = Field(
        default=7200,  # 2 hours
        description="Absolute maximum lifetime for a lease (acquired_at to now)"
    )

    # Task retries
    default_max_attempts: int = Field(default=2, description="Default max task attempts")
    default_retry_backoff_seconds: int = Field(default=15, description="Default retry backoff")
    max_retry_backoff_seconds: int = Field(default=900, description="Max retry backoff (15 min)")

    # Task ordering
    default_priority: int = Field(default=0, description="Default task priority")

    # Pagination
    default_list_limit: int = Field(default=50, description="Default list limit")
    max_list_limit: int = Field(default=200, description="Max list limit")

    # Bootstrap
    default_bootstrap_max_items: int = Field(default=50, description="Default bootstrap items")
    max_bootstrap_max_items: int = Field(default=200, description="Max bootstrap items")

    # Receipt retention
    receipt_retention_days: int = Field(default=30, description="Active receipt retention")
    task_retention_days: int = Field(default=7, description="Terminal task retention")

    # Security (v0 - simple shared token)
    api_key: Optional[str] = None
    allow_insecure_dev: bool = Field(default=False, description="Allow unauthenticated in dev")
    
    # CORS configuration (P0.3 - explicit allowlist)
    cors_allowed_origins: list[str] = Field(
        default=["http://localhost:3000", "http://localhost:8080"],
        description="Allowed CORS origins (explicit allowlist for security)"
    )
    cors_allow_credentials: bool = Field(
        default=True,
        description="Allow credentials in CORS requests"
    )
    cors_allowed_methods: list[str] = Field(
        default=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        description="Allowed HTTP methods"
    )
    cors_allowed_headers: list[str] = Field(
        default=["Authorization", "Content-Type", "X-Tenant-ID"],
        description="Allowed request headers"
    )

    # JWT settings (for OAuth)
    jwt_algorithm: str = "RS256"
    jwt_private_key_path: Optional[str] = None
    jwt_public_key_path: Optional[str] = None
    jwt_access_token_ttl_days: int = 30
    jwt_refresh_token_ttl_days: int = 90

    # Server
    host: str = "0.0.0.0"
    port: int = 8080


settings = Settings()
