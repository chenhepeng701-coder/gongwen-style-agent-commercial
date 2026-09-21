"""Health-check endpoint."""

from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/api/health")
async def health_check() -> dict:
    """Return service health status."""
    return {
        "status": "ok",
        "phase": "Phase1",
        "step": "STEP1",
    }
