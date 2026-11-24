"""Create the minimal active-state schema.

Revision ID: 0001_initial
Revises:
"""
from alembic import op
import json
from pathlib import Path
import sqlalchemy as sa

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Freeze the old bootstrap schema instead of importing future model fields.
    statements = json.loads((Path(__file__).parents[1] / "initial_schema.json").read_text(encoding="utf-8"))
    for statement in statements:
        op.execute(sa.text(statement))


def downgrade() -> None:
    statements = json.loads((Path(__file__).parents[1] / "initial_schema.json").read_text(encoding="utf-8"))
    for statement in reversed(statements):
        if statement.startswith("CREATE TABLE "):
            op.execute(sa.text("DROP TABLE IF EXISTS " + statement.split()[2]))
