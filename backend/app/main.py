import logging
import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi import Request
from fastapi.responses import JSONResponse

from app.documents import router as documents_router
from app.conversations import router as conversations_router
from app.rag import router as rag_router

app = FastAPI(title="Nova AI Assistant API")
logger = logging.getLogger(__name__)


@app.exception_handler(Exception)
async def unexpected_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.error(
        "Unexpected API error for %s %s: %s.",
        request.method,
        request.url.path,
        exc,
        exc_info=(type(exc), exc, exc.__traceback__),
    )
    return JSONResponse(
        status_code=500,
        content={"detail": "An unexpected server error occurred. Please try again."},
    )

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        origin.strip()
        for origin in os.getenv("FRONTEND_ORIGIN", "http://localhost:3000").split(",")
        if origin.strip()
    ],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
)

app.include_router(documents_router)
app.include_router(conversations_router)
app.include_router(rag_router)


@app.get("/health")
async def health_check() -> dict[str, str]:
    """Report that the API is running."""
    return {"status": "ok"}
