"""Add principal_ai and payload_pointer to tasks."""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Upgrade schema with principal_ai and payload_pointer columns."""
    op.add_column(
        "tasks",
        sa.Column("payload_pointer", sa.Text(), nullable=True),
    )
    op.add_column(
        "tasks",
        sa.Column("principal_ai", sa.String(length=255), nullable=True),
    )

    # Backfill existing rows for compatibility
    op.execute(
        sa.text(
            "UPDATE tasks SET principal_ai = created_by_id "
            "WHERE principal_ai IS NULL"
        )
    )
    op.execute(
        sa.text(
            "UPDATE tasks SET payload_pointer = 'inline://task/' || task_id::text "
            "WHERE payload_pointer IS NULL"
        )
    )

    op.create_index(
        "idx_tasks_principal_ai",
        "tasks",
        ["tenant_id", "principal_ai"],
    )


def downgrade() -> None:
    """Downgrade schema to remove principal_ai and payload_pointer."""
    op.drop_index("idx_tasks_principal_ai", table_name="tasks")
    op.drop_column("tasks", "principal_ai")
    op.drop_column("tasks", "payload_pointer")
