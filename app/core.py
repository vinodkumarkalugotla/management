"""Config, database engine, password/token helpers and small utilities."""
import datetime as dt
import os

import bcrypt
import jwt
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from sqlalchemy.pool import NullPool


def _database_url() -> str:
    url = os.getenv("DATABASE_URL", "").strip() or "sqlite:///./hrms.db"
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    return url


DATABASE_URL = _database_url()
IS_SQLITE = DATABASE_URL.startswith("sqlite")

if IS_SQLITE and os.getenv("VERCEL"):
    raise RuntimeError(
        "Vercel has no permanent disk, so SQLite cannot be used there. "
        "Set the DATABASE_URL environment variable to a Postgres connection string (e.g. Neon)."
    )

if IS_SQLITE:
    engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
else:
    # Serverless functions open short-lived connections; let the DB provider pool them.
    engine = create_engine(DATABASE_URL, poolclass=NullPool, pool_pre_ping=True)

SessionLocal = sessionmaker(engine, autoflush=False, expire_on_commit=False)

SECRET_KEY = os.getenv("SECRET_KEY", "dev-only-secret-change-me-in-production-0123456789")
TOKEN_HOURS = 12


class Base(DeclarativeBase):
    pass


def get_db():
    with SessionLocal() as db:
        yield db


def now_utc() -> dt.datetime:
    """Naive UTC timestamp (all datetimes are stored as UTC)."""
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)


def iso(value):
    """Datetime -> ISO string with Z suffix so browsers parse it as UTC."""
    return value.isoformat() + "Z" if isinstance(value, dt.datetime) else None


def parse_dt(value: str) -> dt.datetime:
    parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(dt.timezone.utc).replace(tzinfo=None)
    return parsed


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode(), hashed.encode())
    except ValueError:
        return False


def make_token(user_id: int) -> str:
    exp = now_utc() + dt.timedelta(hours=TOKEN_HOURS)
    return jwt.encode({"sub": str(user_id), "exp": exp}, SECRET_KEY, algorithm="HS256")


def decode_token(token: str) -> int:
    return int(jwt.decode(token, SECRET_KEY, algorithms=["HS256"])["sub"])
