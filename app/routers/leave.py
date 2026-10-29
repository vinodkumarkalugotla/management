import datetime as dt
import os
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..core import get_db, iso, now_utc
from ..deps import current_user, names_map, require_roles, team_ids
from ..models import LeaveRequest, LeaveType, User

router = APIRouter(prefix="/api")

# Weekday numbers that are not working days (Mon=0 ... Sun=6). Default: Sunday only.
WEEKEND = {int(x) for x in os.getenv("WEEKEND_DAYS", "6").split(",") if x.strip()}
ACTIVE = ("pending", "approved")


def working_days(start: dt.date, end: dt.date, half_day: bool) -> float:
    if end < start:
        raise HTTPException(400, "End date is before start date")
    if (end - start).days > 365:
        raise HTTPException(400, "Leave cannot be longer than a year")
    if start.year != end.year:
        raise HTTPException(400, "Split leave that crosses a year end into two requests")
    count, day = 0, start
    while day <= end:
        if day.weekday() not in WEEKEND:
            count += 1
        day += dt.timedelta(days=1)
    if count == 0:
        raise HTTPException(400, "Those dates are all non-working days")
    if half_day:
        if start != end:
            raise HTTPException(400, "Half day leave must be a single date")
        return 0.5
    return float(count)


def _used(db: Session, employee_id: int, type_id: int, year: int, statuses=ACTIVE) -> float:
    q = select(func.coalesce(func.sum(LeaveRequest.days), 0)).where(
        LeaveRequest.employee_id == employee_id,
        LeaveRequest.leave_type_id == type_id,
        LeaveRequest.status.in_(statuses),
        LeaveRequest.start_date >= dt.date(year, 1, 1),
        LeaveRequest.start_date <= dt.date(year, 12, 31),
    )
    return float(db.scalar(q))


def _row_json(r: LeaveRequest, names: dict, types: dict) -> dict:
    return {
        "id": r.id,
        "employee_id": r.employee_id,
        "employee": names.get(r.employee_id),
        "leave_type": types.get(r.leave_type_id),
        "start_date": r.start_date.isoformat(),
        "end_date": r.end_date.isoformat(),
        "half_day": r.half_day,
        "days": r.days,
        "reason": r.reason,
        "status": r.status,
        "decided_by": names.get(r.decided_by),
        "decided_at": iso(r.decided_at),
        "decision_note": r.decision_note,
        "created_at": iso(r.created_at),
    }


def _types_map(db: Session) -> dict:
    return {t.id: t.name for t in db.scalars(select(LeaveType))}


@router.get("/leave-types")
def leave_types(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return [{"id": t.id, "name": t.name, "days_per_year": t.days_per_year} for t in db.scalars(select(LeaveType).order_by(LeaveType.id))]


class LeaveTypeUpdate(BaseModel):
    days_per_year: float


@router.patch("/leave-types/{type_id}")
def update_leave_type(type_id: int, body: LeaveTypeUpdate, user: User = Depends(require_roles("hr")), db: Session = Depends(get_db)):
    lt = db.get(LeaveType, type_id)
    if not lt:
        raise HTTPException(404, "Leave type not found")
    if body.days_per_year < 0:
        raise HTTPException(400, "Days must be 0 or more")
    lt.days_per_year = body.days_per_year
    db.commit()
    return {"ok": True}


@router.get("/leave/balance")
def balance(year: Optional[int] = None, user: User = Depends(current_user), db: Session = Depends(get_db)):
    year = year or dt.date.today().year
    out = []
    for t in db.scalars(select(LeaveType).order_by(LeaveType.id)):
        approved = _used(db, user.id, t.id, year, ("approved",))
        pending = _used(db, user.id, t.id, year, ("pending",))
        out.append({
            "leave_type_id": t.id,
            "leave_type": t.name,
            "allocated": t.days_per_year,
            "approved": approved,
            "pending": pending,
            "remaining": t.days_per_year - approved - pending,
        })
    return out


class LeaveIn(BaseModel):
    leave_type_id: int
    start_date: dt.date
    end_date: dt.date
    half_day: bool = False
    reason: str = ""


@router.post("/leave")
def request_leave(body: LeaveIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    lt = db.get(LeaveType, body.leave_type_id)
    if not lt:
        raise HTTPException(400, "Unknown leave type")
    days = working_days(body.start_date, body.end_date, body.half_day)

    overlap = db.scalar(
        select(func.count(LeaveRequest.id)).where(
            LeaveRequest.employee_id == user.id,
            LeaveRequest.status.in_(ACTIVE),
            LeaveRequest.start_date <= body.end_date,
            LeaveRequest.end_date >= body.start_date,
        )
    )
    if overlap:
        raise HTTPException(400, "You already have a leave request on these dates")

    remaining = lt.days_per_year - _used(db, user.id, lt.id, body.start_date.year)
    if days > remaining:
        raise HTTPException(400, f"Not enough {lt.name} balance: {remaining:g} day(s) left, {days:g} requested")

    req = LeaveRequest(
        employee_id=user.id, leave_type_id=lt.id, start_date=body.start_date, end_date=body.end_date,
        half_day=body.half_day, days=days, reason=body.reason.strip()[:500],
    )
    db.add(req)
    db.commit()
    return _row_json(req, names_map(db), _types_map(db))


@router.get("/leave/mine")
def my_leave(user: User = Depends(current_user), db: Session = Depends(get_db)):
    rows = db.scalars(select(LeaveRequest).where(LeaveRequest.employee_id == user.id).order_by(LeaveRequest.start_date.desc()))
    names, types = names_map(db), _types_map(db)
    return [_row_json(r, names, types) for r in rows]


@router.post("/leave/{leave_id}/cancel")
def cancel(leave_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    req = db.get(LeaveRequest, leave_id)
    if not req or req.employee_id != user.id:
        raise HTTPException(404, "Leave request not found")
    if req.status == "pending" or (req.status == "approved" and req.start_date > dt.date.today()):
        req.status = "cancelled"
        db.commit()
        return {"ok": True}
    raise HTTPException(400, "This request can no longer be cancelled")


@router.get("/leave/approvals")
def approvals(status: str = "pending", user: User = Depends(require_roles("hr", "manager")), db: Session = Depends(get_db)):
    """Requests this user can decide: HR sees all, a manager sees their direct reports."""
    q = select(LeaveRequest).join(User, User.id == LeaveRequest.employee_id).where(LeaveRequest.employee_id != user.id)
    if user.role != "hr":
        q = q.where(User.approver_id == user.id)
    if status != "all":
        q = q.where(LeaveRequest.status == status)
    rows = db.scalars(q.order_by(LeaveRequest.start_date))
    names, types = names_map(db), _types_map(db)
    return [_row_json(r, names, types) for r in rows]


class DecisionIn(BaseModel):
    approve: bool
    note: str = ""


@router.post("/leave/{leave_id}/decision")
def decide(leave_id: int, body: DecisionIn, user: User = Depends(require_roles("hr", "manager")), db: Session = Depends(get_db)):
    req = db.get(LeaveRequest, leave_id)
    if not req:
        raise HTTPException(404, "Leave request not found")
    if req.employee_id == user.id:
        raise HTTPException(403, "You cannot decide your own leave")
    employee = db.get(User, req.employee_id)
    if user.role != "hr" and employee.approver_id != user.id:
        raise HTTPException(403, "You are not this employee's approver")
    if req.status != "pending":
        raise HTTPException(400, f"This request is already {req.status}")
    req.status = "approved" if body.approve else "rejected"
    req.decided_by, req.decided_at, req.decision_note = user.id, now_utc(), body.note.strip()[:500]
    db.commit()
    return _row_json(req, names_map(db), _types_map(db))
