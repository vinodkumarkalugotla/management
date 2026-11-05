"""Wipe ALL data and recreate the first HR admin.

Usage (PowerShell):
    $env:DATABASE_URL = "postgresql://..."      # Neon connection string
    python scripts/reset_db.py <admin_email> <admin_password>
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

if len(sys.argv) != 3:
    sys.exit("Usage: python scripts/reset_db.py <admin_email> <admin_password>")

os.environ["ADMIN_EMAIL"], os.environ["ADMIN_PASSWORD"] = sys.argv[1], sys.argv[2]

from app.core import Base, DATABASE_URL, engine  # noqa: E402
from app.main import init_db  # noqa: E402
from app import models  # noqa: E402,F401  (registers tables)

target = engine.url.render_as_string(hide_password=True)
print(f"Target database: {target}")
if input("This DELETES ALL DATA. Type 'yes' to continue: ").strip() != "yes":
    sys.exit("Aborted.")

Base.metadata.drop_all(engine)
init_db()
print(f"Done. HR login: {sys.argv[1]}")
