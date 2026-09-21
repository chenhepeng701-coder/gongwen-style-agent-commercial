"""Shared API error helpers."""

import logging
from typing import Any

from fastapi import HTTPException


GENERIC_INTERNAL_ERROR = {
    "error_type": "INTERNAL_ERROR",
    "message": "服务暂时不可用，请稍后重试。",
}


def internal_error(logger: logging.Logger, event: str, exc: Exception) -> HTTPException:
    """Log full internal details while returning a stable public error."""
    logger.exception(event, exc_info=exc)
    return HTTPException(status_code=500, detail=GENERIC_INTERNAL_ERROR)


def public_batch_error(label: str) -> dict[str, Any]:
    return {"item": label, "message": GENERIC_INTERNAL_ERROR["message"]}
