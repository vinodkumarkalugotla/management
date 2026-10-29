import datetime as dt
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..core import get_db, hash_password, make_token, verify_password
from ..deps import current_user, require_roles, team_ids
from ..models import User
from .onboarding import create_items_for

router = APIRouter(prefix="/api")

ROLES = {"employee", "manager", "hr"}


def user_json(u: User) -> dict:
    return {
        "id": u.id,
        "name": u.name,
        "email": u.email,
        "role": u.role,
        "approver_id": u.approver_id,
        "join_date": u.join_date.isoformat() if u.join_date else None,
        "is_active": u.is_active,
    }


class LoginIn(BaseModel):
    email: str
    password: str
    role: Optional[str] = None  # portal chosen on the login screen


@router.post("/auth/login")
def login(body: LoginIn, db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.email == body.email.strip().lower()))
    if not user or not user.is_active or not verify_password(body.password, user.password_hash):
        raise HTTPException(401, "Wrong email or password")
    if body.role and body.role != user.role:
        raise HTTPException(403, f"This account is not a{'n' if user.role == 'employee' else ''} {user.role} account. Go back and choose the right portal.")
    return {"token": make_token(user.id), "user": user_json(user)}


@router.get("/me")
def me(user: User = Depends(current_user)):
    return user_json(user)


class PasswordIn(BaseModel):
    old_password: str
    new_password: str


@router.post("/me/password")
def change_password(body: PasswordIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    if not verify_password(body.old_password, user.password_hash):
        raise HTTPException(400, "Current password is wrong")
    if len(body.new_password) < 8:
        raise HTTPException(400, "New password must be at least 8 characters")
    user.password_hash = hash_password(body.new_password)
    db.commit()
    return {"ok": True}


@router.get("/employees")
def list_employees(user: User = Depends(require_roles("hr", "manager")), db: Session = Depends(get_db)):
    ids = team_ids(db, user)
    q = select(User).order_by(User.name)
    if ids is not None:
        q = q.where(User.id.in_(ids))
    return [user_json(u) for u in db.scalars(q)]


@router.get("/approvers")
def list_approvers(user: User = Depends(require_roles("hr")), db: Session = Depends(get_db)):
    q = select(User).where(User.role.in_(["manager", "hr"]), User.is_active.is_(True)).order_by(User.name)
    return [{"id": u.id, "name": u.name} for u in db.scalars(q)]


class EmployeeIn(BaseModel):
    name: str
    email: str
    password: str
    role: str = "employee"
    approver_id: Optional[int] = None
    join_date: Optional[dt.date] = None


@router.post("/employees")
def create_employee(body: EmployeeIn, user: User = Depends(require_roles("hr")), db: Session = Depends(get_db)):
    if body.role not in ROLES:
        raise HTTPException(400, "Invalid role")
    if len(body.password) < 8:
        raise HTTPException(400, "Password must be at least 8 characters")
    emp = User(
        name=body.name.strip(),
        email=body.email.strip().lower(),
        password_hash=hash_password(body.password),
        role=body.role,
        approver_id=body.approver_id,
        join_date=body.join_date or dt.date.today(),
    )
    db.add(emp)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(400, "An employee with this email already exists")
    create_items_for(db, emp.id)
    db.commit()
    return user_json(emp)


class EmployeeUpdate(BaseModel):
    name: Optional[str] = None
    role: Optional[str] = None
    approver_id: Optional[int] = None
    clear_approver: bool = False
    is_active: Optional[bool] = None
    password: Optional[str] = None


@router.patch("/employees/{employee_id}")
def update_employee(employee_id: int, body: EmployeeUpdate, user: User = Depends(require_roles("hr")), db: Session = Depends(get_db)):
    emp = db.get(User, employee_id)
    if not emp:
        raise HTTPException(404, "Employee not found")
    if body.name:
        emp.name = body.name.strip()
    if body.role:
        if body.role not in ROLES:
            raise HTTPException(400, "Invalid role")
        if emp.id == user.id and body.role != "hr":
            raise HTTPException(400, "You cannot remove your own HR role")
        emp.role = body.role
    if body.clear_approver:
        emp.approver_id = None
    elif body.approver_id is not None:
        if body.approver_id == emp.id:
            raise HTTPException(400, "An employee cannot be their own approver")
        emp.approver_id = body.approver_id
    if body.is_active is not None:
        if emp.id == user.id and not body.is_active:
            raise HTTPException(400, "You cannot disable your own account")
        emp.is_active = body.is_active
    if body.password:
        if len(body.password) < 8:
            raise HTTPException(400, "Password must be at least 8 characters")
        emp.password_hash = hash_password(body.password)
    db.commit()
    return user_json(emp)
