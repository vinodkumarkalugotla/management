# Own HRMS

A small HR app with only three features:

1. **Onboarding** - a checklist per new employee (tick-off tasks and document uploads). HR edits the template.
2. **Leave requests and approval** - employees request leave, their approver (manager) or HR approves/rejects. Balances are tracked per leave type.
3. **Check-in / check-out records** - one button; HR can correct a wrong time.

Plus website login/logout, with roles `employee`, `manager`, `hr`.

**Stack:** FastAPI (Python) + SQLAlchemy, one static page (Vue from a CDN, no build step). Backend and frontend are one project and one deploy.

```
api/index.py        Vercel entry point
app/                backend (models, routes)
public/index.html   the whole frontend
vercel.json         routes /api/* to the backend
```

## Roles

| Role | Can do |
|---|---|
| employee | check in/out, request leave, do own onboarding tasks |
| manager | the above + approve leave and see check-ins/onboarding of employees whose *approver* is them |
| hr | everything: add employees, approve any leave, correct times, edit onboarding template and leave days |

Leave counts working days only (Sunday is a day off by default; change with `WEEKEND_DAYS`).
Nobody can approve their own leave.

## Run locally (SQLite, nothing to install but Python)

```
python -m venv .venv
.venv\Scripts\activate            # Mac/Linux: source .venv/bin/activate
pip install -r requirements.txt
uvicorn api.index:app --reload
```
Open http://localhost:8000 and log in with `admin@example.com` / `admin12345`
(set `ADMIN_EMAIL` / `ADMIN_PASSWORD` in the environment first to choose your own).
Data is stored in the file `hrms.db`.

## Free database for Vercel (Neon Postgres)

Vercel has no permanent disk, so SQLite will not work there. Use a free Postgres:

1. Sign up at https://neon.tech, create a project.
2. Copy the **connection string** (looks like `postgresql://user:pass@ep-xxx.neon.tech/neondb?sslmode=require`).
3. Use it as `DATABASE_URL` (below). Tables are created automatically on first request - no SQL to run.

Supabase works the same way (Project Settings -> Database -> connection string, use the pooled one).
To test Neon from your own PC, put the same string in `DATABASE_URL` before running `uvicorn`.

## Deploy to Vercel

1. Push this folder to a GitHub repository.
2. On https://vercel.com click **Add New -> Project**, import the repository. Leave the build settings as they are.
3. Add these **Environment Variables**:

   | Name | Value |
   |---|---|
   | `DATABASE_URL` | the Neon connection string |
   | `SECRET_KEY` | a long random string: `python -c "import secrets; print(secrets.token_urlsafe(48))"` |
   | `ADMIN_EMAIL` | your HR login email |
   | `ADMIN_PASSWORD` | your first HR password |
   | `WEEKEND_DAYS` | optional, e.g. `5,6` for Sat+Sun (default `6`) |

4. Deploy. Open the URL, log in as the HR admin, change the password, then add employees under **Employees**.

Or from the command line: `npm i -g vercel`, then `vercel`, then `vercel --prod`.

Every `git push` redeploys automatically.

## Other hosting (Docker)

```
docker build -t own-hrms .
docker run -p 8000:8000 -v hrms-data:/data own-hrms
```
Uses SQLite in the `hrms-data` volume, or pass `-e DATABASE_URL=postgresql://...`.

## Notes and limits

- Uploaded onboarding files (PDF/PNG/JPG, max 4 MB each - Vercel's request limit) are stored inside the database. For 10 employees that is about 100 MB, well inside Neon's free 0.5 GB. For many more employees, move files to S3/Blob storage.
- Times are stored in UTC and shown in each user's local time.
- Neon's free database sleeps when idle; the first request after a pause takes about a second longer.
- Vercel's free Hobby plan is for non-commercial use; use the Pro plan for company use.
