"""Explicit, idempotent demo seed. Never modifies an existing demo's movements."""
from sqlalchemy import select

from .db import SessionLocal
from .models import Stack
from .services import register_stack, seed_config


def main():
    with SessionLocal.begin() as db:
        seed_config(db)
        stack = db.scalar(select(Stack).where(Stack.is_demo.is_(True)))
        if stack:
            print(f"Existing DEMO stack preserved: {stack.id}")
            return
        stack = register_stack(db, {"project_number": "DEMO-2026-001", "material_ref": "DEMO-COTTON",
                                    "test_code": "ASTM-D5034", "preconditioning_code": "AR", "specimen_count": 20},
                               is_demo=True, actor="demo-seed")
        print(f"Created clearly marked DEMO stack: {stack.id}")


if __name__ == "__main__":
    main()
