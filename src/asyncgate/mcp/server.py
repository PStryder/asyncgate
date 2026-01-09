"""MCP server implementation."""

import copy
from typing import Any
from uuid import UUID

from mcp.server import Server
from mcp.types import Tool, TextContent

from asyncgate.auth.token import verify_auth_token
from asyncgate.db.base import get_session
from asyncgate.engine import (
    AsyncGateEngine,
    InvalidStateTransition,
    LeaseInvalidOrExpired,
    TaskNotFound,
)
from asyncgate.models import Principal, PrincipalKind
from asyncgate.observability.trace import set_trace_id


def _with_auth_schema(schema: dict[str, Any]) -> dict[str, Any]:
    updated = copy.deepcopy(schema)
    updated.setdefault("properties", {})
    updated["properties"]["auth_token"] = {
        "type": "string",
        "description": "API key or JWT for authentication",
    }
    updated["properties"]["trace_id"] = {
        "type": "string",
        "description": "Optional trace ID for correlation",
    }
    required = set(updated.get("required", []))
    required.add("auth_token")
    updated["required"] = sorted(required)
    return updated


def _extract_principal_id(arguments: dict[str, Any]) -> str | None:
    for key in ("agent_id", "worker_id", "principal_id", "to_id"):
        value = arguments.get(key)
        if value:
            return value
    return None


def create_mcp_server() -> Server:
    """Create and configure MCP server."""
    server = Server("asyncgate")

    # ========================================================================
    # Tool definitions
    # ========================================================================

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        """List available tools."""
        return [
            # TASKER tools
            Tool(
                name="asyncgate.bootstrap",
                description="Establish session identity and get attention-aware status",
                inputSchema=_with_auth_schema({
                    "type": "object",
                    "properties": {
                        "agent_id": {"type": "string", "description": "Agent identifier"},
                        "agent_instance_id": {"type": "string", "description": "Optional session ID"},
                        "agent_version": {"type": "string", "description": "Agent version"},
                        "since_receipt_id": {"type": "string", "description": "Cursor for incremental fetch"},
                        "max_items": {"type": "integer", "description": "Max items to return"},
                        "tenant_id": {"type": "string", "description": "Tenant ID"},
                    },
                    "required": ["agent_id", "tenant_id"],
                }),
            ),
            Tool(
                name="asyncgate.create_task",
                description="Create a new async task",
                inputSchema=_with_auth_schema({
                    "type": "object",
                    "properties": {
                        "type": {"type": "string", "description": "Task type"},
                        "payload": {"type": "object", "description": "Task payload"},
                        "requirements": {"type": "object", "description": "Task requirements"},
                        "expected_outcome_kind": {"type": "string", "description": "Expected outcome kind"},
                        "expected_artifact_mime": {"type": "string", "description": "Expected artifact MIME"},
                        "priority": {"type": "integer", "description": "Priority (higher = urgent)"},
                        "idempotency_key": {"type": "string", "description": "Idempotency key"},
                        "max_attempts": {"type": "integer", "description": "Max retry attempts"},
                        "retry_backoff_seconds": {"type": "integer", "description": "Retry backoff"},
                        "delay_seconds": {"type": "integer", "description": "Delay before eligible"},
                        "agent_id": {"type": "string", "description": "Creating agent ID"},
                        "tenant_id": {"type": "string", "description": "Tenant ID"},
                    },
                    "required": ["type", "payload", "agent_id", "tenant_id"],
                }),
            ),
            Tool(
                name="asyncgate.get_task",
                description="Get a task by ID including result if terminal",
                inputSchema=_with_auth_schema({
                    "type": "object",
                    "properties": {
                        "task_id": {"type": "string", "description": "Task UUID"},
                        "tenant_id": {"type": "string", "description": "Tenant ID"},
                    },
                    "required": ["task_id", "tenant_id"],
                }),
            ),
            Tool(
                name="asyncgate.list_tasks",
                description="List tasks with optional filtering",
                inputSchema=_with_auth_schema({
                    "type": "object",
                    "properties": {
                        "status": {"type": "string", "description": "Filter by status"},
                        "type": {"type": "string", "description": "Filter by type"},
                        "limit": {"type": "integer", "description": "Max results"},
                        "cursor": {"type": "string", "description": "Pagination cursor"},
                        "tenant_id": {"type": "string", "description": "Tenant ID"},
                    },
                    "required": ["tenant_id"],
                }),
            ),
            Tool(
                name="asyncgate.cancel_task",
                description="Cancel a task",
                inputSchema=_with_auth_schema({
                    "type": "object",
                    "properties": {
                        "task_id": {"type": "string", "description": "Task UUID"},
                        "reason": {"type": "string", "description": "Cancellation reason"},
                        "agent_id": {"type": "string", "description": "Agent ID"},
                        "tenant_id": {"type": "string", "description": "Tenant ID"},
                    },
                    "required": ["task_id", "agent_id", "tenant_id"],
                }),
            ),
            Tool(
                name="asyncgate.list_receipts",
                description="List receipts for a principal",
                inputSchema=_with_auth_schema({
                    "type": "object",
                    "properties": {
                        "to_kind": {"type": "string", "description": "Recipient kind"},
                        "to_id": {"type": "string", "description": "Recipient ID"},
                        "since_receipt_id": {"type": "string", "description": "Cursor"},
                        "limit": {"type": "integer", "description": "Max results"},
                        "tenant_id": {"type": "string", "description": "Tenant ID"},
                    },
                    "required": ["to_kind", "to_id", "tenant_id"],
                }),
            ),
            Tool(
                name="asyncgate.ack_receipt",
                description="Acknowledge a receipt",
                inputSchema=_with_auth_schema({
                    "type": "object",
                    "properties": {
                        "receipt_id": {"type": "string", "description": "Receipt UUID"},
                        "agent_id": {"type": "string", "description": "Agent ID"},
                        "tenant_id": {"type": "string", "description": "Tenant ID"},
                    },
                    "required": ["receipt_id", "agent_id", "tenant_id"],
                }),
            ),
            # TASKEE tools
            Tool(
                name="asyncgate.lease_next",
                description="Claim next available tasks matching capabilities",
                inputSchema=_with_auth_schema({
                    "type": "object",
                    "properties": {
                        "worker_id": {"type": "string", "description": "Worker identifier"},
                        "capabilities": {"type": "array", "items": {"type": "string"}, "description": "Worker capabilities"},
                        "accept_types": {"type": "array", "items": {"type": "string"}, "description": "Task types to accept"},
                        "max_tasks": {"type": "integer", "description": "Max tasks to claim"},
                        "lease_ttl_seconds": {"type": "integer", "description": "Lease TTL"},
                        "tenant_id": {"type": "string", "description": "Tenant ID"},
                    },
                    "required": ["worker_id", "tenant_id"],
                }),
            ),
            Tool(
                name="asyncgate.renew_lease",
                description="Renew an active lease",
                inputSchema=_with_auth_schema({
                    "type": "object",
                    "properties": {
                        "worker_id": {"type": "string", "description": "Worker ID"},
                        "task_id": {"type": "string", "description": "Task UUID"},
                        "lease_id": {"type": "string", "description": "Lease UUID"},
                        "extend_by_seconds": {"type": "integer", "description": "Extension duration"},
                        "tenant_id": {"type": "string", "description": "Tenant ID"},
                    },
                    "required": ["worker_id", "task_id", "lease_id", "tenant_id"],
                }),
            ),
            Tool(
                name="asyncgate.report_progress",
                description="Report task execution progress",
                inputSchema=_with_auth_schema({
                    "type": "object",
                    "properties": {
                        "worker_id": {"type": "string", "description": "Worker ID"},
                        "task_id": {"type": "string", "description": "Task UUID"},
                        "lease_id": {"type": "string", "description": "Lease UUID"},
                        "progress": {"type": "object", "description": "Progress data"},
                        "tenant_id": {"type": "string", "description": "Tenant ID"},
                    },
                    "required": ["worker_id", "task_id", "lease_id", "progress", "tenant_id"],
                }),
            ),
            Tool(
                name="asyncgate.start_task",
                description="Mark a task as running",
                inputSchema=_with_auth_schema({
                    "type": "object",
                    "properties": {
                        "worker_id": {"type": "string", "description": "Worker ID"},
                        "task_id": {"type": "string", "description": "Task UUID"},
                        "lease_id": {"type": "string", "description": "Lease UUID"},
                        "tenant_id": {"type": "string", "description": "Tenant ID"},
                    },
                    "required": ["worker_id", "task_id", "lease_id", "tenant_id"],
                }),
            ),
            Tool(
                name="asyncgate.complete",
                description="Mark a task as successfully completed",
                inputSchema=_with_auth_schema({
                    "type": "object",
                    "properties": {
                        "worker_id": {"type": "string", "description": "Worker ID"},
                        "task_id": {"type": "string", "description": "Task UUID"},
                        "lease_id": {"type": "string", "description": "Lease UUID"},
                        "result": {"type": "object", "description": "Task result"},
                        "artifacts": {"type": "object", "description": "Result artifacts"},
                        "tenant_id": {"type": "string", "description": "Tenant ID"},
                    },
                    "required": ["worker_id", "task_id", "lease_id", "result", "tenant_id"],
                }),
            ),
            Tool(
                name="asyncgate.fail",
                description="Mark a task as failed",
                inputSchema=_with_auth_schema({
                    "type": "object",
                    "properties": {
                        "worker_id": {"type": "string", "description": "Worker ID"},
                        "task_id": {"type": "string", "description": "Task UUID"},
                        "lease_id": {"type": "string", "description": "Lease UUID"},
                        "error": {"type": "object", "description": "Error details"},
                        "retryable": {"type": "boolean", "description": "Whether to retry"},
                        "tenant_id": {"type": "string", "description": "Tenant ID"},
                    },
                    "required": ["worker_id", "task_id", "lease_id", "error", "tenant_id"],
                }),
            ),
            Tool(
                name="asyncgate.get_config",
                description="Get server configuration",
                inputSchema=_with_auth_schema({"type": "object", "properties": {}}),
            ),
        ]

    # ========================================================================
    # Tool handlers
    # ========================================================================

    @server.call_tool()
    async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
        """Handle tool calls."""
        import json

        try:
            result = await _handle_tool(name, arguments)
            return [TextContent(type="text", text=json.dumps(result, indent=2))]
        except Exception as e:
            error_result = {"error": str(e), "code": getattr(e, "code", "ERROR")}
            return [TextContent(type="text", text=json.dumps(error_result, indent=2))]

    return server


async def _handle_tool(name: str, arguments: dict[str, Any]) -> Any:
    """Route tool call to appropriate handler."""
    async with get_session() as session:
        engine = AsyncGateEngine(session)
        auth_token = arguments.pop("auth_token", None)
        trace_id = arguments.pop("trace_id", None)
        set_trace_id(trace_id)
        principal_id = _extract_principal_id(arguments)
        tenant_id_value = arguments.get("tenant_id")
        auth = await verify_auth_token(
            auth_token,
            session,
            tenant_id=tenant_id_value,
            principal_id=principal_id,
        )

        # TASKER tools
        if name == "asyncgate.bootstrap":
            principal = Principal(
                kind=PrincipalKind.AGENT,
                id=arguments["agent_id"],
                instance_id=arguments.get("agent_instance_id"),
            )
            return await engine.bootstrap(
                tenant_id=UUID(arguments["tenant_id"]),
                principal=principal,
                since_receipt_id=UUID(arguments["since_receipt_id"]) if arguments.get("since_receipt_id") else None,
                max_items=arguments.get("max_items"),
            )

        elif name == "asyncgate.create_task":
            created_by = Principal(
                kind=PrincipalKind.AGENT,
                id=arguments["agent_id"],
            )
            return await engine.create_task(
                tenant_id=UUID(arguments["tenant_id"]),
                type=arguments["type"],
                payload=arguments["payload"],
                created_by=created_by,
                requirements=arguments.get("requirements"),
                expected_outcome_kind=arguments.get("expected_outcome_kind"),
                expected_artifact_mime=arguments.get("expected_artifact_mime"),
                priority=arguments.get("priority"),
                idempotency_key=arguments.get("idempotency_key"),
                max_attempts=arguments.get("max_attempts"),
                retry_backoff_seconds=arguments.get("retry_backoff_seconds"),
                delay_seconds=arguments.get("delay_seconds"),
                actor_is_internal=auth.is_internal,
            )

        elif name == "asyncgate.get_task":
            return await engine.get_task(
                tenant_id=UUID(arguments["tenant_id"]),
                task_id=UUID(arguments["task_id"]),
            )

        elif name == "asyncgate.list_tasks":
            return await engine.list_tasks(
                tenant_id=UUID(arguments["tenant_id"]),
                status=arguments.get("status"),
                type=arguments.get("type"),
                limit=arguments.get("limit"),
                cursor=arguments.get("cursor"),
            )

        elif name == "asyncgate.cancel_task":
            principal = Principal(
                kind=PrincipalKind.AGENT,
                id=arguments["agent_id"],
            )
            return await engine.cancel_task(
                tenant_id=UUID(arguments["tenant_id"]),
                task_id=UUID(arguments["task_id"]),
                principal=principal,
                reason=arguments.get("reason"),
                actor_is_internal=auth.is_internal,
            )

        elif name == "asyncgate.list_receipts":
            return await engine.list_receipts(
                tenant_id=UUID(arguments["tenant_id"]),
                to_kind=arguments["to_kind"],
                to_id=arguments["to_id"],
                since_receipt_id=UUID(arguments["since_receipt_id"]) if arguments.get("since_receipt_id") else None,
                limit=arguments.get("limit"),
            )

        elif name == "asyncgate.ack_receipt":
            principal = Principal(
                kind=PrincipalKind.AGENT,
                id=arguments["agent_id"],
            )
            return await engine.ack_receipt(
                tenant_id=UUID(arguments["tenant_id"]),
                receipt_id=UUID(arguments["receipt_id"]),
                principal=principal,
            )

        # TASKEE tools
        elif name == "asyncgate.lease_next":
            return await engine.lease_next(
                tenant_id=UUID(arguments["tenant_id"]),
                worker_id=arguments["worker_id"],
                capabilities=arguments.get("capabilities"),
                accept_types=arguments.get("accept_types"),
                max_tasks=arguments.get("max_tasks", 1),
                lease_ttl_seconds=arguments.get("lease_ttl_seconds"),
            )

        elif name == "asyncgate.renew_lease":
            return await engine.renew_lease(
                tenant_id=UUID(arguments["tenant_id"]),
                worker_id=arguments["worker_id"],
                task_id=UUID(arguments["task_id"]),
                lease_id=UUID(arguments["lease_id"]),
                extend_by_seconds=arguments.get("extend_by_seconds"),
            )

        elif name == "asyncgate.report_progress":
            return await engine.report_progress(
                tenant_id=UUID(arguments["tenant_id"]),
                worker_id=arguments["worker_id"],
                task_id=UUID(arguments["task_id"]),
                lease_id=UUID(arguments["lease_id"]),
                progress_data=arguments["progress"],
            )

        elif name == "asyncgate.start_task":
            return await engine.start_task(
                tenant_id=UUID(arguments["tenant_id"]),
                worker_id=arguments["worker_id"],
                task_id=UUID(arguments["task_id"]),
                lease_id=UUID(arguments["lease_id"]),
            )

        elif name == "asyncgate.complete":
            return await engine.complete(
                tenant_id=UUID(arguments["tenant_id"]),
                worker_id=arguments["worker_id"],
                task_id=UUID(arguments["task_id"]),
                lease_id=UUID(arguments["lease_id"]),
                result=arguments["result"],
                artifacts=arguments.get("artifacts"),
            )

        elif name == "asyncgate.fail":
            return await engine.fail(
                tenant_id=UUID(arguments["tenant_id"]),
                worker_id=arguments["worker_id"],
                task_id=UUID(arguments["task_id"]),
                lease_id=UUID(arguments["lease_id"]),
                error=arguments["error"],
                retryable=arguments.get("retryable", False),
            )

        elif name == "asyncgate.get_config":
            return await engine.get_config()

        else:
            raise ValueError(f"Unknown tool: {name}")
