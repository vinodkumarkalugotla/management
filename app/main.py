import logging
import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from .core import Base, SessionLocal, engine, hash_password
from .models import LeaveType, OnboardingTemplateTask, User
from .routers import attendance, leave, onboarding, people

log = logging.getLogger("hrms")

DEFAULT_LEAVE_TYPES = [("Casual Leave", 12), ("Sick Leave", 10), ("Paid Leave", 15)]
DEFAULT_ONBOARDING = [
    ("Complete your profile details", False),
    ("Upload ID proof", True),
    ("Upload signed offer letter", True),
    ("Upload bank account details", True),
    ("Attend company orientation", False),
]


def init_db() -> None:
    """Create tables and seed leave types, onboarding template and the first HR admin."""
    Base.metadata.create_all(engine)
    try:
        with SessionLocal() as db:
            if not db.scalar(select(LeaveType.id).limit(1)):
                db.add_all(LeaveType(name=n, days_per_year=d) for n, d in DEFAULT_LEAVE_TYPES)
            if not db.scalar(select(OnboardingTemplateTask.id).limit(1)):
                db.add_all(
                    OnboardingTemplateTask(title=t, requires_upload=u, position=i)
                    for i, (t, u) in enumerate(DEFAULT_ONBOARDING, start=1)
                )
            if not db.scalar(select(User.id).limit(1)):
                email = os.getenv("ADMIN_EMAIL", "admin@example.com").strip().lower()
                password = os.getenv("ADMIN_PASSWORD", "admin12345")
                db.add(User(name="HR Admin", email=email, password_hash=hash_password(password), role="hr"))
                log.warning("Created first HR admin %s - change the password after logging in", email)
            db.commit()
    except IntegrityError:
        pass  # another cold start seeded at the same moment


app = FastAPI(title="Own HRMS")
for module in (people, leave, attendance, onboarding):
    app.include_router(module.router)


@app.get("/api/health")
def health():
    return {"ok": True}


init_db()

# Local / Docker only: on Vercel the files in public/ are served by its CDN.
public_dir = Path(__file__).resolve().parent.parent / "public"
if public_dir.is_dir():
    app.mount("/", StaticFiles(directory=public_dir, html=True), name="public")
