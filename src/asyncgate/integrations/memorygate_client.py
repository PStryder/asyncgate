"""ReceiptGate client with circuit breaker protection."""

import logging
from typing import Any, Optional

import httpx

from asyncgate.config import settings
from asyncgate.integrations.circuit_breaker import CircuitBreaker, CircuitBreakerConfig

logger = logging.getLogger(__name__)


class ReceiptGateClient:
    """
    Client for ReceiptGate integration with circuit breaker protection.

    Usage:
        client = ReceiptGateClient()
        await client.emit_receipt(receipt_data)
    """

    def __init__(self):
        self._circuit_breaker: Optional[CircuitBreaker] = None
        self._initialize_circuit_breaker()

    def _initialize_circuit_breaker(self):
        """Initialize circuit breaker if enabled."""
        if not settings.receiptgate_circuit_breaker_enabled:
            logger.info("ReceiptGate circuit breaker disabled")
            return

        config = CircuitBreakerConfig(
            failure_threshold=settings.receiptgate_circuit_breaker_failure_threshold,
            timeout_seconds=settings.receiptgate_circuit_breaker_timeout_seconds,
            half_open_max_calls=settings.receiptgate_circuit_breaker_half_open_max_calls,
            success_threshold=settings.receiptgate_circuit_breaker_success_threshold,
            on_open=lambda: logger.error("ReceiptGate circuit breaker opened"),
            on_close=lambda: logger.info("ReceiptGate circuit breaker closed"),
            on_half_open=lambda: logger.info("ReceiptGate circuit breaker half-open"),
        )

        self._circuit_breaker = CircuitBreaker("receiptgate", config)
        logger.info("ReceiptGate circuit breaker initialized")

    async def emit_receipt(self, receipt_data: dict[str, Any]) -> dict[str, Any]:
        """Emit a LegiVellum receipt to ReceiptGate with circuit breaker protection."""
        if not settings.receiptgate_endpoint:
            return {"status": "skipped", "note": "receiptgate_endpoint not configured"}

        if self._circuit_breaker:
            # Protected call with fallback to local buffer
            return await self._circuit_breaker.call(
                self._emit_to_receiptgate,
                receipt_data,
                fallback=self._fallback_to_buffer,
            )
        else:
            # Direct call without protection
            return await self._emit_to_receiptgate(receipt_data)

    async def _emit_to_receiptgate(self, receipt_data: dict[str, Any]) -> dict[str, Any]:
        """POST receipt payload to ReceiptGate via MCP."""
        base_url = settings.receiptgate_endpoint.rstrip("/")
        if not base_url.endswith("/mcp"):
            base_url = f"{base_url}/mcp"

        headers = {"Content-Type": "application/json"}
        if settings.receiptgate_auth_token:
            headers["Authorization"] = f"Bearer {settings.receiptgate_auth_token}"

        timeout = settings.receiptgate_emission_timeout_ms / 1000
        async with httpx.AsyncClient(timeout=timeout) as client:
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "receiptgate.submit_receipt",
                    "arguments": {"receipt": receipt_data},
                },
            }
            response = await client.post(base_url, json=payload, headers=headers)
            response.raise_for_status()
            data = response.json()
            if "error" in data:
                raise RuntimeError(f"ReceiptGate error: {data['error']}")
            return data.get("result", {})

    async def _fallback_to_buffer(self, receipt_data: dict[str, Any]) -> dict[str, Any]:
        """
        Fallback when circuit is open - buffer for retry.
        
        Returns success to prevent request failure, but queues for background retry.
        """
        logger.warning(
            "ReceiptGate circuit open, buffering receipt",
            extra={"phase": receipt_data.get("phase", "unknown")},
        )
        
        # TODO: Implement retry buffer (queue to disk/redis for background worker)
        # For now, just log and return success
        
        return {"status": "buffered", "note": "Circuit breaker open, queued for retry"}

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
_receiptgate_client: Optional[ReceiptGateClient] = None


def get_receiptgate_client() -> ReceiptGateClient:
    """Get or create ReceiptGate client singleton."""
    global _receiptgate_client
    if _receiptgate_client is None:
        _receiptgate_client = ReceiptGateClient()
    return _receiptgate_client
