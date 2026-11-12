"""Edit a registered stack after its labels are printed.

Label codes are specimen ids, and nothing here changes an id or a specimen's number, so
every printed label keeps scanning to the same physical piece. Added specimens continue
the stack's numbering and need only their own new labels. A removed specimen keeps its
number and history; scanning its label is refused with a clear message.
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select

from .db import get_db
from .models import AuditEvent, Material, Project, Specimen, Stack, Treatment, utcnow
from .schemas import AddSpecimensBody, RemoveSpecimensBody, StackEditBody
from .services import current_session, find_or_create, iso, stack_detail
from sqlalchemy.dialects.postgresql import insert

router = APIRouter(prefix="/api/stacks", tags=["Stack editing"])


def _lock(db, stack_id, expected_version):
    stack = db.scalar(select(Stack).where(Stack.id == stack_id).with_for_update().execution_options(populate_existing=True))
    if stack is None:
        raise HTTPException(404, "Stack not found.")
    if expected_version is not None and stack.version != expected_version:
        raise HTTPException(409, "The stack changed. Reload it before saving.")
    return stack


def _describe(stack):
    return {"project_number": stack.project.project_number, "material_ref": stack.material.material_ref,
            "test_code": stack.test_code, "preconditioning_code": stack.preconditioning_code}


@router.patch("/{stack_id}")
def edit_stack(stack_id: int, body: StackEditBody, db=Depends(get_db)):
    with db.begin():
        stack = _lock(db, stack_id, body.expected_version)
        before = _describe(stack)
        if body.project_number is not None:
            stack.project = find_or_create(db, Project, Project.project_number, body.project_number)
        if body.material_ref is not None:
            stack.material = find_or_create(db, Material, Material.material_ref, body.material_ref)
        if body.test_code is not None:
            stack.test_code = body.test_code
        if body.preconditioning_code is not None:
            db.execute(insert(Treatment).values(code=body.preconditioning_code).on_conflict_do_nothing())
            stack.preconditioning_code = body.preconditioning_code
        db.flush()
        db.refresh(stack)
        after = _describe(stack)
        changed = {key: {"before": before[key], "after": after[key]} for key in after if before[key] != after[key]}
        if not changed:
            return stack_detail(db, stack)
        live = [p.id for p in db.scalars(select(Specimen).where(Specimen.stack_id == stack.id, Specimen.removed_at.is_(None)))]
        db.add(AuditEvent(stack_id=stack.id, kind="stack_edit", actor="project-staff", reason=body.note or None,
                          specimen_ids=live, payload={"changes": changed, "occurred_at": iso(utcnow())}))
        stack.version += 1
        db.flush()
        return stack_detail(db, stack)


@router.post("/{stack_id}/specimens")
def add_specimens(stack_id: int, body: AddSpecimensBody, db=Depends(get_db)):
    with db.begin():
        stack = _lock(db, stack_id, body.expected_version)
        members = list(db.scalars(select(Specimen).where(Specimen.stack_id == stack.id)))
        if sum(p.removed_at is None for p in members) + body.count > 500:
            raise HTTPException(422, "A stack can hold at most 500 specimens.")
        start = max((p.number for p in members), default=0) + 1   # removed numbers are never reused
        added = [Specimen(stack_id=stack.id, number=start + offset) for offset in range(body.count)]
        db.add_all(added)
        db.flush()
        event = AuditEvent(stack_id=stack.id, kind="specimens_added", actor="project-staff", reason=body.note or None,
                           specimen_ids=[p.id for p in added],
                           payload={"numbers": [p.number for p in added], "occurred_at": iso(utcnow())})
        db.add(event)
        db.flush()
        for specimen in added:
            specimen.last_event_id = event.id
        stack.version += 1
        db.flush()
        return {**stack_detail(db, stack), "added_specimen_ids": [p.id for p in added]}


@router.post("/{stack_id}/specimens/remove")
def remove_specimens(stack_id: int, body: RemoveSpecimensBody, db=Depends(get_db)):
    with db.begin():
        stack = _lock(db, stack_id, body.expected_version)
        ids = list(body.specimen_ids)
        members = {p.id: p for p in db.scalars(select(Specimen).where(Specimen.stack_id == stack.id)
                                               .with_for_update().execution_options(populate_existing=True))}
        if len(set(ids)) != len(ids) or any(i not in members or members[i].removed_at is not None for i in ids):
            raise HTTPException(422, "Choose specimens that are still on this stack.")
        if sum(p.removed_at is None for p in members.values()) - len(ids) < 1:
            raise HTTPException(422, "A stack keeps at least one specimen. Remove the whole stack's labels from use instead.")
        inside = [members[i].number for i in ids if current_session(db, i) is not None]
        if inside:
            raise HTTPException(409, "Check these specimens out of the room first: " + ", ".join(f"{n:02d}" for n in sorted(inside)))
        now = utcnow()
        event = AuditEvent(stack_id=stack.id, kind="specimens_removed", actor="project-staff", reason=body.note or None,
                           specimen_ids=ids, payload={"numbers": sorted(members[i].number for i in ids), "occurred_at": iso(now)})
        db.add(event)
        db.flush()
        for identifier in ids:
            members[identifier].removed_at = now
            members[identifier].revision += 1
            members[identifier].last_event_id = event.id
        stack.version += 1
        db.flush()
        return stack_detail(db, stack)
