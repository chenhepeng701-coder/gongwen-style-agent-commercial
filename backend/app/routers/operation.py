"""Operation planning APIs."""

from fastapi import APIRouter

from app.services.operation.topic_suggestion_service import get_topic_suggestions

router = APIRouter()


@router.get("/topic-suggestions")
async def topic_suggestions():
    """Return read-only topic suggestions from fixed rules and library matches."""
    return get_topic_suggestions()
