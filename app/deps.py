from typing import Optional

import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from .core import decode_token, get_db
from .models import User

_bearer = HTTPBearer(auto_error=False)


def current_user(
    creds: Optional[HTTPAuthorizationCredentials] = Depends(_bearer),
    db: Session = Depends(get_db),
) -> User:
    if not creds:
        raise HTTPException(401, "Not logged in")
    try:
        user = db.get(User, decode_token(creds.credentials))
    except (jwt.PyJWTError, ValueError):
        raise HTTPException(401, "Session expired, please log in again")
    if not user or not user.is_active:
        raise HTTPException(401, "Account not found or disabled")
    return user


def require_roles(*roles: str):
    def checker(user: User = Depends(current_user)) -> User:
        if user.role not in roles:
            raise HTTPException(403, "You do not have permission to do this")
        return user

    return checker


def team_ids(db: Session, user: User):
    """Employee ids this user may see. None means everyone (HR)."""
    if user.role == "hr":
        return None
    ids = [user.id]
    if user.role == "manager":
        ids += list(db.scalars(select(User.id).where(User.approver_id == user.id)))
    return ids


def can_view_employee(db: Session, user: User, employee_id: int) -> bool:
    ids = team_ids(db, user)
    return ids is None or employee_id in ids


def names_map(db: Session) -> dict:
    return {uid: name for uid, name in db.execute(select(User.id, User.name))}
