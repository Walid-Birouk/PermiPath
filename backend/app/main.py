"""FastAPI application entry point."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from app.ask import router as ask_router
from app.config import get_settings


class HealthResponse(BaseModel):
    status: str


def create_app() -> FastAPI:
    settings = get_settings()
    application = FastAPI(title="PermiPath API", version="0.1.0")
    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
    )

    @application.get("/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        # Deliberately touches no external dependency: it is polled during cold starts
        # and by the keep-alive job, and must stay cheap.
        return HealthResponse(status="ok")

    application.include_router(ask_router)
    return application


app = create_app()
