from urllib.parse import quote

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile
from pydantic import BaseModel
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from ..core import get_db, iso, now_utc
from ..deps import current_user, names_map, require_roles, team_ids
from ..models import OnboardingItem, OnboardingTemplateTask, User

router = APIRouter(prefix="/api/onboarding")

MAX_FILE_BYTES = 4 * 1024 * 1024  # Vercel request bodies are limited to ~4.5 MB
ALLOWED_TYPES = {"application/pdf", "image/png", "image/jpeg"}


def create_items_for(db: Session, employee_id: int) -> None:
    """Copy the onboarding template into a checklist for a new employee."""
    tasks = db.scalars(select(OnboardingTemplateTask).order_by(OnboardingTemplateTask.position, OnboardingTemplateTask.id))
    for t in tasks:
        db.add(OnboardingItem(employee_id=employee_id, title=t.title, requires_upload=t.requires_upload))


def _item_json(i: OnboardingItem) -> dict:
    return {
        "id": i.id,
        "employee_id": i.employee_id,
        "title": i.title,
        "requires_upload": i.requires_upload,
        "status": i.status,
        "completed_at": iso(i.completed_at),
        "doc_name": i.doc_name,
    }


def _load_item(db: Session, item_id: int, user: User) -> OnboardingItem:
    item = db.get(OnboardingItem, item_id)
    if not item:
        raise HTTPException(404, "Task not found")
    if user.role != "hr" and item.employee_id != user.id:
        raise HTTPException(403, "Not allowed")
    return item


@router.get("/mine")
def my_items(user: User = Depends(current_user), db: Session = Depends(get_db)):
    rows = db.scalars(select(OnboardingItem).where(OnboardingItem.employee_id == user.id).order_by(OnboardingItem.id))
    return [_item_json(i) for i in rows]


@router.get("/employee/{employee_id}")
def employee_items(employee_id: int, user: User = Depends(require_roles("hr", "manager")), db: Session = Depends(get_db)):
    ids = team_ids(db, user)
    if ids is not None and employee_id not in ids:
        raise HTTPException(403, "Not your team member")
    rows = db.scalars(select(OnboardingItem).where(OnboardingItem.employee_id == employee_id).order_by(OnboardingItem.id))
    return [_item_json(i) for i in rows]


@router.get("/overview")
def overview(user: User = Depends(require_roles("hr", "manager")), db: Session = Depends(get_db)):
    """Progress per employee (HR: everyone, manager: own team)."""
    ids = team_ids(db, user)
    q = select(
        OnboardingItem.employee_id,
        func.count(OnboardingItem.id),
        func.sum(case((OnboardingItem.status == "done", 1), else_=0)),
    ).group_by(OnboardingItem.employee_id)
    names = names_map(db)
    out = []
    for emp_id, total, done in db.execute(q):
        if ids is None or emp_id in ids:
            out.append({"employee_id": emp_id, "employee": names.get(emp_id), "total": total, "done": int(done or 0)})
    return out


@router.post("/items/{item_id}/complete")
def complete(item_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    item = _load_item(db, item_id, user)
    if item.requires_upload and not item.doc_name:
        raise HTTPException(400, "Upload the document to complete this task")
    item.status, item.completed_at = "done", now_utc()
    db.commit()
    return _item_json(item)


@router.post("/items/{item_id}/reopen")
def reopen(item_id: int, user: User = Depends(require_roles("hr")), db: Session = Depends(get_db)):
    item = _load_item(db, item_id, user)
    item.status, item.completed_at = "pending", None
    db.commit()
    return _item_json(item)


@router.post("/items/{item_id}/upload")
async def upload(item_id: int, file: UploadFile = File(...), user: User = Depends(current_user), db: Session = Depends(get_db)):
    item = _load_item(db, item_id, user)
    if file.content_type not in ALLOWED_TYPES:
        raise HTTPException(400, "Only PDF, PNG or JPG files are allowed")
    data = await file.read(MAX_FILE_BYTES + 1)
    if len(data) > MAX_FILE_BYTES:
        raise HTTPException(413, "File is larger than 4 MB")
    item.doc_name, item.doc_type, item.doc_data = file.filename or "document", file.content_type, data
    item.status, item.completed_at = "done", now_utc()
    db.commit()
    return _item_json(item)


@router.get("/items/{item_id}/file")
def download(item_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    item = _load_item(db, item_id, user)
    if not item.doc_name:
        raise HTTPException(404, "No file uploaded")
    return Response(
        item.doc_data,
        media_type=item.doc_type,
        headers={"Content-Disposition": f"inline; filename*=UTF-8''{quote(item.doc_name)}"},
    )


# ---- checklist template (HR) -------------------------------------------------

class TemplateIn(BaseModel):
    title: str
    requires_upload: bool = False


@router.get("/template")
def get_template(user: User = Depends(require_roles("hr")), db: Session = Depends(get_db)):
    rows = db.scalars(select(OnboardingTemplateTask).order_by(OnboardingTemplateTask.position, OnboardingTemplateTask.id))
    return [{"id": t.id, "title": t.title, "requires_upload": t.requires_upload} for t in rows]


@router.post("/template")
def add_template_task(body: TemplateIn, user: User = Depends(require_roles("hr")), db: Session = Depends(get_db)):
    last = db.scalar(select(func.max(OnboardingTemplateTask.position))) or 0
    db.add(OnboardingTemplateTask(title=body.title.strip(), requires_upload=body.requires_upload, position=last + 1))
    db.commit()
    return {"ok": True}


@router.delete("/template/{task_id}")
def delete_template_task(task_id: int, user: User = Depends(require_roles("hr")), db: Session = Depends(get_db)):
    task = db.get(OnboardingTemplateTask, task_id)
    if task:
        db.delete(task)
        db.commit()
    return {"ok": True}


@router.post("/employee/{employee_id}/task")
def add_task_for_employee(employee_id: int, body: TemplateIn, user: User = Depends(require_roles("hr")), db: Session = Depends(get_db)):
    """Add a one-off task to a single employee's checklist."""
    if not db.get(User, employee_id):
        raise HTTPException(404, "Employee not found")
    db.add(OnboardingItem(employee_id=employee_id, title=body.title.strip(), requires_upload=body.requires_upload))
    db.commit()
    return {"ok": True}
