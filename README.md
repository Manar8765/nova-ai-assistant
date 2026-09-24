# Nova AI Assistant

Nova AI Assistant is a Next.js frontend and FastAPI backend for authenticated,
company-scoped document search and grounded conversation history.

## Project structure

```
.
├── frontend/             # Next.js, React, and TypeScript application
├── backend/              # FastAPI application
├── docker-compose.yml    # Production-style local container stack
├── .env.example          # Environment-variable template
└── .gitignore
```

## Prerequisites

- Node.js 20 or later
- Python 3.11 or later
- Docker Desktop (optional, for containers)

## Run locally

Copy the environment template if you need local overrides:

```powershell
Copy-Item .env.example .env
```

### Frontend

```powershell
cd frontend
npm install
npm run dev
```

The frontend starts at http://localhost:3000.

### Backend

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

The health check is available at http://localhost:8000/health and returns:

```json
{"status":"ok"}
```

## Run with Docker

From the project root:

```powershell
Copy-Item .env.example .env
# Fill in the Supabase, Gemini, and Groq values in .env.
docker compose up --build
```

This exposes the frontend on http://localhost:3000 and the backend on http://localhost:8000.
The Compose stack runs the optimized Next.js standalone server and a non-reloading
Uvicorn server without source bind mounts. For live development, use the native
frontend and backend commands above.

The backend health endpoint is `GET /health`; interactive API documentation is
available at http://localhost:8000/docs. `FRONTEND_ORIGIN` accepts a comma-separated
list of allowed browser origins. Public `NEXT_PUBLIC_*` values are supplied as
frontend build arguments; server-only credentials remain runtime backend variables.

## Validation

```powershell
pytest
python -m compileall backend
cd frontend
npm run build
cd ..
docker compose config
```
