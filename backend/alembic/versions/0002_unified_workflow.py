"""Workbook provenance, durable print jobs, and one conditioning period per specimen.

The original migration creates tables from model metadata, so each addition is
conditional to support both a fresh database and an existing v1 installation.
Existing repeated periods are never deleted or silently collapsed.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "0002_unified_workflow"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    additions = {
        "prep_drafts": [("source", "{}"), ("warnings", "[]"), ("sheets", "[]"), ("review_data", "{}")],
        "stacks": [("source_metadata", "{}")],
    }
    for table, columns in additions.items():
        existing = {column["name"] for column in inspector.get_columns(table)}
        for name, default in columns:
            if name not in existing:
                op.add_column(table, sa.Column(name, JSONB(), nullable=False, server_default=sa.text("'" + default + "'::jsonb")))
    op.execute("ALTER TABLE treatments ALTER COLUMN code TYPE VARCHAR(200)")
    op.execute("ALTER TABLE stacks ALTER COLUMN preconditioning_code TYPE VARCHAR(200)")
    duplicates = bind.execute(sa.text("SELECT specimen_id FROM conditioning_sessions WHERE voided = false GROUP BY specimen_id HAVING count(*) > 1 LIMIT 1")).first()
    if duplicates:
        raise RuntimeError("This database contains repeated conditioning periods. Preserve it as an archive and configure a fresh dedicated database for the one-period workflow; no records have been removed.")
    op.execute("CREATE UNIQUE INDEX IF NOT EXISTS one_conditioning_period_per_specimen ON conditioning_sessions(specimen_id) WHERE voided = false")
    from app.models import PrintJob
    PrintJob.__table__.create(bind, checkfirst=True)


def downgrade():
    op.execute("DROP INDEX IF EXISTS one_conditioning_period_per_specimen")
    # Provenance and submitted printer-job records remain available after a rollback.
