#!/usr/bin/env python3
"""Golden path demo for AsyncGate (minimal worker loop)."""

from __future__ import annotations

import json
import os
import sys
import time
from typing import Any
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


def _env(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name)
    if value is None or value == "":
        return default
    return value


class HttpClient:
    def __init__(self, base_url: str, api_key: str | None = None, tenant_id: str | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.headers: dict[str, str] = {"Content-Type": "application/json"}
        if api_key:
            self.headers["Authorization"] = f"Bearer {api_key}"
        if tenant_id:
            self.headers["X-Tenant-ID"] = tenant_id

    def request_json(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        query: dict[str, Any] | None = None,
        timeout: float = 10.0,
    ) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        if query:
            query = {k: v for k, v in query.items() if v is not None}
            if query:
                url = f"{url}?{urlencode(query)}"

        data = None
        if payload is not None:
            data = json.dumps(payload, default=str).encode("utf-8")

        req = Request(url, data=data, method=method)
        for key, value in self.headers.items():
            req.add_header(key, value)

        try:
            with urlopen(req, timeout=timeout) as response:
                raw = response.read()
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"{method} {url} failed: {exc.code} {exc.reason}: {detail}") from None

        if not raw:
            return {}
        return json.loads(raw.decode("utf-8"))


def main() -> int:
    asyncgate_url = _env("ASYNCGATE_URL", "http://localhost:8080")
    api_key = _env("ASYNCGATE_API_KEY")
    tenant_id = _env("ASYNCGATE_TENANT_ID")
    principal_id = _env("ASYNCGATE_PRINCIPAL_ID", "principal-demo")
    worker_id = _env("ASYNCGATE_WORKER_ID", "demo-worker-1")
    task_type = _env("ASYNCGATE_TASK_TYPE", "demo.task")

    client = HttpClient(asyncgate_url, api_key=api_key, tenant_id=tenant_id)

    print("Checking health...")
    health = client.request_json("GET", "/v1/health")
    if health.get("status") != "healthy":
        raise RuntimeError(f"Unexpected health response: {health}")

    payload = {
        "task_summary": "Golden path demo task",
        "message": "Generate a demo artifact.",
        "task_type": task_type,
    }

    print("Creating task...")
    create_resp = client.request_json(
        "POST",
        "/v1/tasks",
        payload={
            "type": task_type,
            "payload": payload,
            "principal_ai": principal_id,
            "expected_outcome_kind": "artifact_pointer",
            "expected_artifact_mime": "text/plain",
        },
        query={"principal_kind": "agent", "principal_id": principal_id},
    )

    task_id = str(create_resp.get("task_id"))
    if not task_id:
        raise RuntimeError(f"Missing task_id in response: {create_resp}")
    print(f"Task created: {task_id}")

    print("Claiming lease...")
    lease_resp = client.request_json(
        "POST",
        "/v1/leases/claim",
        payload={
            "worker_id": worker_id,
            "capabilities": ["demo"],
            "accept_types": [task_type],
            "max_tasks": 1,
        },
    )

    tasks = lease_resp.get("tasks", [])
    if not tasks:
        raise RuntimeError("No tasks claimed; ensure AsyncGate is running and task is queued.")

    lease = tasks[0]
    lease_id = str(lease.get("lease_id"))
    print(f"Lease claimed: {lease_id}")

    client.request_json(
        "POST",
        f"/v1/tasks/{task_id}/running",
        payload={"worker_id": worker_id, "lease_id": lease_id},
    )

    artifact = {
        "type": "demo",
        "uri": f"memory://artifact/{task_id}",
        "mime": "text/plain",
        "bytes": len(task_id),
    }

    result = {
        "summary": "Golden path success",
        "artifact_uri": artifact["uri"],
    }

    print("Completing task...")
    client.request_json(
        "POST",
        f"/v1/tasks/{task_id}/complete",
        payload={
            "worker_id": worker_id,
            "lease_id": lease_id,
            "result": result,
            "artifacts": [artifact],
        },
    )

    time.sleep(0.5)

    task = client.request_json("GET", f"/v1/tasks/{task_id}")
    if task.get("status") != "succeeded":
        raise RuntimeError(f"Task not succeeded yet: {task}")

    receipts = client.request_json(
        "GET",
        "/v1/receipts",
        query={"to_kind": "agent", "to_id": principal_id, "limit": 200},
    ).get("receipts", [])

    receipt_types = {r.get("receipt_type") for r in receipts}
    if "task.assigned" not in receipt_types or "task.completed" not in receipt_types:
        raise RuntimeError(f"Missing expected receipts: {receipt_types}")

    open_obligations = client.request_json(
        "GET",
        "/v1/obligations/open",
        query={"principal_kind": "agent", "principal_id": principal_id, "limit": 50},
    ).get("open_obligations", [])

    open_task_ids = {o.get("task_id") for o in open_obligations}
    if task_id in open_task_ids:
        raise RuntimeError("Obligation still open after completion")

    print("Golden path complete: task succeeded, receipts present, obligation closed.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise
