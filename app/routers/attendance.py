import datetime as dt
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core import get_db, iso, now_utc, parse_dt
from ..deps import current_user, names_map, require_roles, team_ids
from ..models import Checkin, User

router = APIRouter(prefix="/api/attendance")


def _json(c: Checkin, names: dict) -> dict:
    hours = round((c.check_out - c.check_in).total_seconds() / 3600, 2) if c.check_out else None
    return {
        "id": c.id,
        "employee_id": c.employee_id,
        "employee": names.get(c.employee_id),
        "check_in": iso(c.check_in),
        "check_out": iso(c.check_out),
        "hours": hours,
    }


def _open_session(db: Session, employee_id: int) -> Optional[Checkin]:
    q = select(Checkin).where(Checkin.employee_id == employee_id, Checkin.check_out.is_(None)).order_by(Checkin.check_in.desc())
    return db.scalars(q).first()


def _month_range(month: Optional[str]):
    try:
        start = dt.datetime.strptime(month or now_utc().strftime("%Y-%m"), "%Y-%m")
    except ValueError:
        raise HTTPException(400, "month must look like 2026-10")
    end = (start.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
    return start, end


@router.post("/in")
def check_in(user: User = Depends(current_user), db: Session = Depends(get_db)):
    if _open_session(db, user.id):
        raise HTTPException(400, "You are already checked in")
    row = Checkin(employee_id=user.id, check_in=now_utc())
    db.add(row)
    db.commit()
    return _json(row, {user.id: user.name})


@router.post("/out")
def check_out(user: User = Depends(current_user), db: Session = Depends(get_db)):
    row = _open_session(db, user.id)
    if not row:
        raise HTTPException(400, "You are not checked in")
    row.check_out = now_utc()
    db.commit()
    return _json(row, {user.id: user.name})


@router.get("/today")
def today(user: User = Depends(current_user), db: Session = Depends(get_db)):
    open_row = _open_session(db, user.id)
    return {"checked_in": bool(open_row), "since": iso(open_row.check_in) if open_row else None}


@router.get("/mine")
def mine(month: Optional[str] = None, user: User = Depends(current_user), db: Session = Depends(get_db)):
    start, end = _month_range(month)
    q = select(Checkin).where(Checkin.employee_id == user.id, Checkin.check_in >= start, Checkin.check_in < end).order_by(Checkin.check_in.desc())
    names = {user.id: user.name}
    return [_json(c, names) for c in db.scalars(q)]


@router.get("/team")
def team(month: Optional[str] = None, employee_id: Optional[int] = None, user: User = Depends(require_roles("hr", "manager")), db: Session = Depends(get_db)):
    start, end = _month_range(month)
    q = select(Checkin).where(Checkin.check_in >= start, Checkin.check_in < end)
    ids = team_ids(db, user)
    if ids is not None:
        q = q.where(Checkin.employee_id.in_(ids))
    if employee_id:
        q = q.where(Checkin.employee_id == employee_id)
    names = names_map(db)
    return [_json(c, names) for c in db.scalars(q.order_by(Checkin.check_in.desc()))]


class CorrectionIn(BaseModel):
    check_in: str
    check_out: Optional[str] = None


@router.patch("/{checkin_id}")
def correct(checkin_id: int, body: CorrectionIn, user: User = Depends(require_roles("hr")), db: Session = Depends(get_db)):
    row = db.get(Checkin, checkin_id)
    if not row:
        raise HTTPException(404, "Record not found")
    try:
        new_in = parse_dt(body.check_in)
        new_out = parse_dt(body.check_out) if body.check_out else None
    except ValueError:
        raise HTTPException(400, "Invalid date/time")
    if new_out and new_out <= new_in:
        raise HTTPException(400, "Check-out must be after check-in")
    row.check_in, row.check_out, row.edited_by = new_in, new_out, user.id
    db.commit()
    return _json(row, names_map(db))
