"""The whole schema in one step (see docs/DATABASE.md).

The database is created from the models, then the audit table is made append-only.
There is no upgrade path from the UUID-keyed schema: that data was test data, so a
deployment from before sequential IDs starts from an empty database.
"""
from alembic import op
from app.db import Base
from app import models  # noqa: F401  (registers every table on Base.metadata)

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    Base.metadata.create_all(bind=op.get_bind())
    op.execute("""
        CREATE FUNCTION prevent_audit_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'Audit events are append-only';
        END;
        $$;
    """)
    op.execute("""
        CREATE TRIGGER audit_events_immutable
        BEFORE UPDATE OR DELETE OR TRUNCATE ON audit_events
        FOR EACH STATEMENT EXECUTE FUNCTION prevent_audit_mutation();
    """)


def downgrade():
    op.execute("DROP TRIGGER IF EXISTS audit_events_immutable ON audit_events")
    op.execute("DROP FUNCTION IF EXISTS prevent_audit_mutation()")
    Base.metadata.drop_all(bind=op.get_bind())
