-- Add principal_ai and payload_pointer columns (idempotent) and backfill.
-- Ensures principal_ai is populated from created_by_id when missing.

ALTER TABLE tasks
    ADD COLUMN IF NOT EXISTS payload_pointer TEXT;

ALTER TABLE tasks
    ADD COLUMN IF NOT EXISTS principal_ai VARCHAR(255);

CREATE INDEX IF NOT EXISTS idx_tasks_principal_ai
    ON tasks (tenant_id, principal_ai);

UPDATE tasks
SET principal_ai = created_by_id
WHERE principal_ai IS NULL;

UPDATE tasks
SET payload_pointer = 'inline://task/' || task_id::text
WHERE payload_pointer IS NULL;
