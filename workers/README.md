# AsyncGate Workers

Workers are autonomous agents that poll AsyncGate for tasks, execute them, and report results via receipts.

## Architecture

### Core Principle: AsyncGate is NOT a Process Manager

AsyncGate provides **coordination substrate** - it manages the obligation ledger, lease protocol, and receipt chains. It does NOT:
- Spawn worker processes
- Monitor worker health
- Restart crashed workers
- Manage worker lifecycle

Workers are **independent processes** that:
- Poll AsyncGate's `/v1/lease` endpoint
- Declare capabilities on each poll (stateless)
- Accept tasks by emitting receipts
- Execute work autonomously
- Report completion via receipts

### Local vs Remote Workers

The lease protocol is **location-agnostic**. Workers can run:

**Locally** (same machine as AsyncGate):
```bash
python -m workers.command_executor.worker \
  --asyncgate-url http://localhost:8000 \
  --api-key local-key
```

**Remotely** (different machine):
```bash
python worker.py \
  --asyncgate-url https://asyncgate.example.com \
  --api-key remote-key
```

AsyncGate sees no difference - just HTTP requests with capabilities.

## Plugin Structure

Each worker lives in its own directory:

```
workers/
├── command_executor/        # Reference implementation
│   ├── manifest.yaml        # Metadata + capability declarations
│   ├── worker.py            # Main executable
│   ├── requirements.txt     # Python dependencies
│   └── README.md            # Usage documentation
└── {your_worker}/
    ├── manifest.yaml
    ├── worker.py
    └── ...
```

### Manifest Format

```yaml
name: worker_name
version: 1.0.0
description: What this worker does

capabilities:
  - task_type: "capability.name"
    schema:
      type: object
      properties:
        param1: {type: string}
      required: [param1]
```

The manifest is **documentation only** - AsyncGate doesn't read it. Workers declare capabilities directly in lease polls.

## Worker Protocol

### 1. Poll for Tasks

```http
POST /v1/lease
Authorization: Bearer {api_key}

{
  "worker_id": "unique-worker-id",
  "capabilities": ["task.type.one", "task.type.two"]
}
```

**Responses:**
- `204 No Content` - No matching tasks available
- `200 OK` - Task offer with lease

### 2. Accept Task

Emit `accepted` receipt:

```json
{
  "receipt_type": "accepted",
  "parent_receipt_ids": ["{queued_receipt_id}"],
  "body": {
    "worker_id": "unique-worker-id",
    "accepted_at": "2026-01-06T18:00:00Z"
  }
}
```

### 3. Execute Work

Do whatever the task requires - execute commands, call APIs, process data, etc.

### 4. Report Completion

**Success:**
```json
{
  "receipt_type": "success",
  "parent_receipt_ids": ["{accepted_receipt_id}"],
  "body": {
    "completed_at": "2026-01-06T18:01:00Z"
  },
  "artifacts": [{
    "path": "/path/to/output",
    "content_type": "application/json",
    "description": "Task output"
  }]
}
```

**Failure:**
```json
{
  "receipt_type": "failure",
  "parent_receipt_ids": ["{accepted_receipt_id}"],
  "body": {
    "error": "Description of what went wrong",
    "failed_at": "2026-01-06T18:01:00Z"
  }
}
```

## Creating a New Worker

1. **Create directory**: `workers/{worker_name}/`
2. **Add manifest.yaml**: Document capabilities and schemas
3. **Implement worker.py**:
   - Poll `/v1/lease` with capabilities
   - Parse task payload
   - Emit `accepted` receipt
   - Execute work
   - Emit `success`/`failure` receipt
4. **Add README.md**: Usage instructions
5. **Run independently**: `python worker.py --asyncgate-url=... --api-key=...`

## Reference Implementation

See `command_executor/` for a complete working example that:
- Polls for `command.execute` tasks
- Executes shell commands
- Writes output to filesystem
- Reports via receipt chains

This demonstrates the minimal viable worker protocol.

## Design Philosophy

### Workers are Autonomous Agents

Workers manage their own:
- Lifecycle (start/stop/restart)
- Configuration
- Error handling
- Resource management

AsyncGate provides coordination - workers provide execution.

### Capabilities are Declared, Not Registered

No upfront registration. Workers declare capabilities on every lease poll. This keeps the protocol stateless and enables dynamic worker pools.

### Protocol is Pure HTTP

Workers are just HTTP clients. They can be written in any language that can:
- Make HTTP requests
- Parse JSON
- Execute tasks

The reference implementation uses Python, but Go/Rust/Node workers follow the same protocol.

## Security Considerations

Workers authenticate to AsyncGate via API keys. In production:
- Use separate API keys per worker (or worker pool)
- Rotate keys regularly
- Implement network isolation between worker types
- Consider mutual TLS for remote workers
- Sandbox worker execution environments

## Deployment Patterns

### Single-Machine Development

All workers run as separate processes on the same machine as AsyncGate.

### Distributed Production

Workers run on separate machines/containers, polling AsyncGate via HTTPS. Scale by adding more worker processes.

### Hybrid

Critical workers run locally (low latency), heavy/expensive workers run remotely (resource isolation).

## Testing

To test a worker:

1. Start AsyncGate: `uvicorn src.asyncgate.main:app`
2. Start worker: `python worker.py --asyncgate-url=... --api-key=...`
3. Queue task: `curl -X POST .../v1/tasks -d '{task payload}'`
4. Watch worker logs for acceptance and execution
5. Verify receipts via `/v1/receipts` endpoint
6. Check artifacts for output

## License

Part of AsyncGate - see parent repository for license.
