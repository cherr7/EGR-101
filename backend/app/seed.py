"""Explicit, idempotent demo seed. Never modifies an existing demo's movements."""
from sqlalchemy import select

from .db import SessionLocal
from .models import PrepDraft, Stack
from .services import commit_draft, seed_config


def main():
    with SessionLocal.begin() as db:
        seed_config(db)
        stack = db.scalar(select(Stack).where(Stack.is_demo.is_(True)))
        if stack:
            print(f"Existing DEMO stack preserved: {stack.id}")
            return
        draft = PrepDraft(rows=[{"project_number": "DEMO-2026-001", "material_ref": "DEMO-COTTON",
                                "test_code": "ASTM-D5034", "preconditioning_code": "AR", "specimen_count": 20}])
        db.add(draft)
        db.flush()
        ids = commit_draft(db, draft, is_demo=True)
        print(f"Created clearly marked DEMO stack: {ids[0]}")


if __name__ == "__main__":
    main()
