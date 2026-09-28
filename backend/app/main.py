"""FastAPI application entry point for gongwen-style-agent."""

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.routers import health, settings, library, retrieval, generation, operation
from app.utils.runtime_paths import initialize_runtime_environment, get_output_dir
from app.utils.frontend_hosting import mount_frontend


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan: create runtime directories on startup."""
    initialize_runtime_environment()
    yield


app = FastAPI(
    title="gongwen-style-agent",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://localhost:5177",
        "http://127.0.0.1:5173",
        "http://127.0.0.1:5177",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Static mounts
_output_dir = get_output_dir()
if _output_dir.exists():
    app.mount("/static/output", StaticFiles(directory=str(_output_dir)), name="output")

# Routers
app.include_router(health.router)
app.include_router(settings.router, prefix='/api/settings', tags=['settings'])
app.include_router(library.router, prefix='/api/library', tags=['library'])
app.include_router(retrieval.router, prefix='/api/retrieval', tags=['retrieval'])
app.include_router(generation.router, prefix='/api/generation', tags=['generation'])
app.include_router(operation.router, prefix='/api/operation', tags=['operation'])


if not mount_frontend(app):
    @app.get("/")
    async def root() -> dict:
        return {"message": "gongwen-style-agent backend is running."}
