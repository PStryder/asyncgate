"""MemoryGate client with circuit breaker protection."""

import logging
from typing import Any, Optional
from uuid import UUID

from asyncgate.config import settings
from asyncgate.integrations.circuit_breaker import CircuitBreaker, CircuitBreakerConfig

logger = logging.getLogger(__name__)


class MemoryGateClient:
    """
    Client for MemoryGate integration with circuit breaker protection.
    
    Usage:
        client = MemoryGateClient()
        await client.emit_receipt(receipt_data)
    """

    def __init__(self):
        self._circuit_breaker: Optional[CircuitBreaker] = None
        self._initialize_circuit_breaker()

    def _initialize_circuit_breaker(self):
        """Initialize circuit breaker if enabled."""
        if not settings.memorygate_circuit_breaker_enabled:
            logger.info("MemoryGate circuit breaker disabled")
            return

        config = CircuitBreakerConfig(
            failure_threshold=settings.memorygate_circuit_breaker_failure_threshold,
            timeout_seconds=settings.memorygate_circuit_breaker_timeout_seconds,
            half_open_max_calls=settings.memorygate_circuit_breaker_half_open_max_calls,
            success_threshold=settings.memorygate_circuit_breaker_success_threshold,
            on_open=lambda: logger.error("MemoryGate circuit breaker opened"),
            on_close=lambda: logger.info("MemoryGate circuit breaker closed"),
            on_half_open=lambda: logger.info("MemoryGate circuit breaker half-open"),
        )

        self._circuit_breaker = CircuitBreaker("memorygate", config)
        logger.info("MemoryGate circuit breaker initialized")

    async def emit_receipt(
        self,
        tenant_id: UUID,
        receipt_type: str,
        from_principal: dict[str, str],
        to_principal: dict[str, str],
        task_id: Optional[UUID] = None,
        lease_id: Optional[UUID] = None,
        body: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """
        Emit receipt to MemoryGate with circuit breaker protection.
        
        Args:
            tenant_id: Tenant identifier
            receipt_type: Type of receipt
            from_principal: Sender principal (kind, id)
            to_principal: Recipient principal (kind, id)
            task_id: Optional task ID
            lease_id: Optional lease ID
            body: Optional receipt body
            
        Returns:
            Response from MemoryGate or fallback response
            
        Raises:
            Exception: If circuit breaker is disabled and call fails
        """
        receipt_data = {
            "tenant_id": str(tenant_id),
            "receipt_type": receipt_type,
            "from": from_principal,
            "to": to_principal,
            "task_id": str(task_id) if task_id else None,
            "lease_id": str(lease_id) if lease_id else None,
            "body": body or {},
        }

        if self._circuit_breaker:
            # Protected call with fallback to local buffer
            return await self._circuit_breaker.call(
                self._emit_to_memorygate,
                receipt_data,
                fallback=self._fallback_to_buffer,
            )
        else:
            # Direct call without protection
            return await self._emit_to_memorygate(receipt_data)

    async def _emit_to_memorygate(self, receipt_data: dict[str, Any]) -> dict[str, Any]:
        """
        Actual MemoryGate API call (to be implemented).
        
        Placeholder for now - would use aiohttp to POST to MemoryGate.
        """
        # TODO: Implement actual MemoryGate HTTP client
        # Example:
        # async with aiohttp.ClientSession() as session:
        #     async with session.post(
        #         f"{settings.memorygate_url}/api/receipts",
        #         json=receipt_data,
        #         headers={"Authorization": f"Bearer {settings.memorygate_token}"},
        #         timeout=aiohttp.ClientTimeout(
        #             total=settings.memorygate_emission_timeout_ms / 1000
        #         ),
        #     ) as response:
        #         response.raise_for_status()
        #         return await response.json()
        
        logger.debug(f"Would emit receipt to MemoryGate: {receipt_data['receipt_type']}")
        return {"status": "queued_locally", "note": "MemoryGate client not yet implemented"}

    async def _fallback_to_buffer(self, receipt_data: dict[str, Any]) -> dict[str, Any]:
        """
        Fallback when circuit is open - buffer for retry.
        
        Returns success to prevent request failure, but queues for background retry.
        """
        logger.warning(
            f"MemoryGate circuit open, buffering receipt: "
            f"{receipt_data['receipt_type']}"
        )
        
        # TODO: Implement retry buffer (queue to disk/redis for background worker)
        # For now, just log and return success
        
        return {
            "status": "buffered",
            "note": "Circuit breaker open, queued for retry",
        }

    def get_circuit_stats(self) -> Optional[dict[str, Any]]:
        """Get circuit breaker statistics."""
        if not self._circuit_breaker:
            return None

        stats = self._circuit_breaker.stats
        return {
            "state": stats.state.value,
            "failure_count": stats.failure_count,
            "success_count": stats.success_count,
            "total_calls": stats.total_calls,
            "total_failures": stats.total_failures,
            "total_successes": stats.total_successes,
            "opened_at": stats.opened_at.isoformat() if stats.opened_at else None,
            "last_failure_time": (
                stats.last_failure_time.isoformat() if stats.last_failure_time else None
            ),
        }

    async def reset_circuit(self):
        """Manually reset circuit breaker."""
        if self._circuit_breaker:
            await self._circuit_breaker.reset()


# Singleton instance
_memorygate_client: Optional[MemoryGateClient] = None


def get_memorygate_client() -> MemoryGateClient:
    """Get or create MemoryGate client singleton."""
    global _memorygate_client
    if _memorygate_client is None:
        _memorygate_client = MemoryGateClient()
    return _memorygate_client
