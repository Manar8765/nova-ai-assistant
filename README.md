# Nova AI Assistant

Initial MVP project scaffold with a Next.js frontend and a FastAPI backend.

## Project structure

```
.
├── frontend/             # Next.js, React, and TypeScript application
├── backend/              # FastAPI application
├── docker-compose.yml    # Local development containers
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
docker compose up --build
```

This exposes the frontend on http://localhost:3000 and the backend on http://localhost:8000.
