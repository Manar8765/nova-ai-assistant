import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.documents import router as documents_router

app = FastAPI(title="Nova AI Assistant API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[os.getenv("FRONTEND_ORIGIN", "http://localhost:3000")],
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
)

app.include_router(documents_router)


@app.get("/health")
async def health_check() -> dict[str, str]:
    """Report that the API is running."""
    return {"status": "ok"}
