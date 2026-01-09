"""Adapter for rendering AsyncGate receipts in MemoryGate schema."""

import json
from typing import Any

from asyncgate.models import Principal, Receipt, ReceiptType, Task
from asyncgate.models.enums import Outcome


def _principal_value(principal: Principal) -> str:
    return principal.id


def _extract_artifact_fields(artifacts: Any) -> dict[str, Any]:
    artifact = None
    if isinstance(artifacts, list) and artifacts:
        artifact = artifacts[0]
    elif isinstance(artifacts, dict):
        artifact = artifacts

    if not isinstance(artifact, dict):
        return {
            "artifact_location": "NA",
            "artifact_pointer": "NA",
            "artifact_checksum": "NA",
            "artifact_size_bytes": 0,
            "artifact_mime": "NA",
        }

    artifact_pointer = (
        artifact.get("url")
        or artifact.get("uri")
        or artifact.get("output_path")
        or artifact.get("pointer")
        or "NA"
    )
    artifact_location = artifact.get("type") or artifact.get("store") or "NA"
    artifact_mime = (
        artifact.get("mime")
        or artifact.get("content_type")
        or artifact.get("artifact_mime")
        or "NA"
    )
    artifact_checksum = artifact.get("checksum") or artifact.get("etag") or "NA"
    artifact_size_bytes = artifact.get("size_bytes") or artifact.get("bytes") or 0

    return {
        "artifact_location": artifact_location,
        "artifact_pointer": artifact_pointer,
        "artifact_checksum": artifact_checksum,
        "artifact_size_bytes": artifact_size_bytes,
        "artifact_mime": artifact_mime,
    }


def _derive_phase_and_status(receipt: Receipt, task: Task | None) -> tuple[str, str]:
    if receipt.receipt_type == ReceiptType.TASK_ESCALATED:
        return "escalate", "NA"

    if receipt.receipt_type in {
        ReceiptType.TASK_COMPLETED,
        ReceiptType.TASK_FAILED,
        ReceiptType.TASK_CANCELED,
        ReceiptType.TASK_RESULT_READY,
    }:
        if receipt.receipt_type == ReceiptType.TASK_FAILED:
            return "complete", "failure"
        if receipt.receipt_type == ReceiptType.TASK_CANCELED:
            return "complete", "canceled"
        if receipt.receipt_type == ReceiptType.TASK_RESULT_READY and task and task.result:
            if task.result.outcome == Outcome.FAILED:
                return "complete", "failure"
            if task.result.outcome == Outcome.CANCELED:
                return "complete", "canceled"
            return "complete", "success"
        return "complete", "success"

    return "accepted", "NA"


def _derive_outcome_kind(receipt: Receipt, task: Task | None) -> str:
    body = receipt.body or {}
    artifacts = body.get("artifacts")
    result_payload = body.get("result_payload")
    error_payload = body.get("error")

    has_artifacts = artifacts is not None
    has_result = result_payload is not None
    has_error = error_payload is not None

    if has_artifacts and has_result:
        return "mixed"
    if has_artifacts:
        return "artifact_pointer"
    if has_result or has_error:
        return "response_text"
    if task and task.result:
        return "response_text"
    return "NA"


def _task_summary(receipt: Receipt, task: Task | None) -> str:
    body = receipt.body or {}
    if receipt.receipt_type == ReceiptType.TASK_ASSIGNED and "instructions" in body:
        return body["instructions"]
    if "result_summary" in body:
        return body["result_summary"]
    if isinstance(body.get("error"), dict) and body["error"].get("message"):
        return body["error"]["message"]
    if task:
        return task.type
    return "NA"


def to_memorygate_receipt(receipt: Receipt, task: Task | None) -> dict[str, Any]:
    """Convert AsyncGate receipt to MemoryGate receipt schema."""
    body = receipt.body or {}
    phase, status = _derive_phase_and_status(receipt, task)

    outcome_kind = _derive_outcome_kind(receipt, task)
    outcome_text = body.get("result_summary") or "NA"
    if outcome_text == "NA" and isinstance(body.get("error"), dict):
        outcome_text = body["error"].get("message") or "NA"

    artifact_fields = _extract_artifact_fields(body.get("artifacts"))

    expected_outcome_kind = (
        task.expected_outcome_kind if task and task.expected_outcome_kind else "NA"
    )
    expected_artifact_mime = (
        task.expected_artifact_mime if task and task.expected_artifact_mime else "NA"
    )

    created_at = receipt.created_at.isoformat() if receipt.created_at else None
    started_at = task.started_at.isoformat() if task and task.started_at else None
    completed_at = None
    if task and task.result and task.result.completed_at:
        completed_at = task.result.completed_at.isoformat()

    inputs = task.payload if task else {}
    task_body = json.dumps(inputs) if inputs else ""

    caused_by = str(receipt.parents[0]) if receipt.parents else "NA"

    escalation_fields = {
        "escalation_class": body.get("escalation_class", "NA"),
        "escalation_reason": body.get("escalation_reason", "NA"),
        "escalation_to": body.get("escalation_to", "NA"),
        "retry_requested": body.get("retry_requested", False),
    }

    metadata = {
        "receipt_type": receipt.receipt_type.value,
        "lease_id": str(receipt.lease_id) if receipt.lease_id else "NA",
        "parents": [str(parent) for parent in receipt.parents],
        "from_kind": receipt.from_.kind.value,
        "to_kind": receipt.to_.kind.value,
    }
    if "trace_id" in body:
        metadata["trace_id"] = body["trace_id"]

    return {
        "schema_version": "1.0",
        "tenant_id": str(receipt.tenant_id),
        "receipt_id": str(receipt.receipt_id),
        "task_id": str(receipt.task_id) if receipt.task_id else "NA",
        "parent_task_id": "NA",
        "caused_by_receipt_id": caused_by,
        "dedupe_key": receipt.hash or "NA",
        "attempt": task.attempt if task else 0,
        "from_principal": _principal_value(receipt.from_),
        "for_principal": _principal_value(receipt.to_),
        "source_system": "asyncgate",
        "recipient_ai": _principal_value(receipt.to_),
        "trust_domain": "default",
        "phase": phase,
        "status": status,
        "realtime": False,
        "task_type": task.type if task else "NA",
        "task_summary": _task_summary(receipt, task),
        "task_body": task_body,
        "inputs": inputs,
        "expected_outcome_kind": expected_outcome_kind,
        "expected_artifact_mime": expected_artifact_mime,
        "outcome_kind": outcome_kind,
        "outcome_text": outcome_text,
        **artifact_fields,
        **escalation_fields,
        "created_at": created_at,
        "stored_at": created_at,
        "started_at": started_at,
        "completed_at": completed_at,
        "read_at": None,
        "archived_at": None,
        "metadata": metadata,
    }
