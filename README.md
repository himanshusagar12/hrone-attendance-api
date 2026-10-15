# Employee Attendance & Analytics API

FastAPI + MongoDB implementation for the HROne Software Engineer Trainee assignment.

## Requirements

- Python 3.11+
- MongoDB 6.0+
- Dependencies in `requirements.txt`

## Run locally

Create and activate a virtual environment, then install dependencies:

```bash
python -m venv .venv
# Windows PowerShell
.venv\\Scripts\\Activate.ps1
# macOS/Linux
# source .venv/bin/activate
python -m pip install -r requirements.txt
```

Start MongoDB separately (for example, your local MongoDB service or MongoDB Atlas). Configure environment variables before starting the API.

Windows PowerShell:

```powershell
$env:MONGO_URI="mongodb://localhost:27017"
$env:MONGO_DB="attendance_db"
python -m uvicorn app.main:app --port 8000
```

macOS/Linux:

```bash
export MONGO_URI="mongodb://localhost:27017"
export MONGO_DB="attendance_db"
python -m uvicorn app.main:app --port 8000
```

Open `http://127.0.0.1:8000/docs`. `GET /health` pings MongoDB and returns 503 if the database is unavailable. Indexes are created at application startup.

## Tests

Run the database-free business-rule tests:

```bash
python -m unittest discover -s tests
```

These tests cover grace-period boundaries, overnight attendance dates, overtime/work-hour calculations, half-up rounding, and epoch-millisecond validation. They do **not** replace integration tests against a running MongoDB 6.0+ server.

Before submission, verify on a real MongoDB instance:
1. `GET /health` returns 200.
2. Employee creation and duplicate employee codes (201 then 409).
3. Punch-in, duplicate punch-in (201 then 409), punch-out and concurrent punch-out.
4. Manual correction, audit history, no-op correction (422), and invalid punch dates.
5. All four analytics routes and the `/admin/explain/{endpoint}` routes on seeded data.

## Files

- `app/main.py` — API implementation
- `requirements.txt` — runtime dependencies
- `README.md` — setup and test instructions
- `REVIEW.md` — review of the supplied starter code
- `DECISIONS.md` — design decisions
- `tests/` — database-free business-rule tests

## Configuration and security

Set `MONGO_URI` and `MONGO_DB` as environment variables. Do not commit `.env` files, database credentials, access tokens, or other secrets. No Dockerfile is included.
