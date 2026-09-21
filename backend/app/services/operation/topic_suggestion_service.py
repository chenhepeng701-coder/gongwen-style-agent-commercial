"""Read-only topic suggestion service for operation planning."""

import json
from datetime import date
from pathlib import Path
from typing import Any

from app.services.library.indexer import list_documents

TOPIC_CALENDAR_PATH = Path(__file__).resolve().parents[2] / "data" / "topic_calendar.json"
PRIORITY_ORDER = {"高": 0, "中": 1, "低": 2}


def load_topic_rules() -> list[dict[str, Any]]:
    """Load the fixed topic calendar rule library."""
    if not TOPIC_CALENDAR_PATH.exists():
        return []
    data = json.loads(TOPIC_CALENDAR_PATH.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        return []
    return [item for item in data if isinstance(item, dict)]


def get_topic_suggestions(today: date | None = None) -> list[dict[str, Any]]:
    """Return current-month topic suggestions with lightweight material matching."""
    current_date = today or date.today()
    docs = list_documents()["items"]
    suggestions = []

    for rule in load_topic_rules():
        if not _applies_to_month(rule.get("month"), current_date.month):
            continue
        keywords = _normalize_string_list(rule.get("keywords"))
        material_count = _count_matching_documents(docs, keywords)
        suggestions.append(
            {
                "topic": str(rule.get("topic", "")),
                "scene": str(rule.get("scene", "")),
                "priority": str(rule.get("priority", "中")),
                "timing_reason": str(rule.get("timing_reason", "")),
                "pain_points": _normalize_string_list(rule.get("pain_points")),
                "keywords": keywords,
                "material_count": material_count,
                "match_level": _match_level(material_count),
                "recommended_mode": str(rule.get("recommended_mode", "公文初稿")),
                "material_suggestions": _normalize_string_list(rule.get("material_suggestions")),
            }
        )

    return sorted(
        suggestions,
        key=lambda item: (
            PRIORITY_ORDER.get(item["priority"], 99),
            -item["material_count"],
            item["topic"],
        ),
    )


def _applies_to_month(value: Any, month: int) -> bool:
    if isinstance(value, int):
        return value == month
    if isinstance(value, list):
        return month in {int(item) for item in value if _can_int(item)}
    return True


def _can_int(value: Any) -> bool:
    try:
        int(value)
        return True
    except (TypeError, ValueError):
        return False


def _normalize_string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _count_matching_documents(docs: list[Any], keywords: list[str]) -> int:
    if not keywords:
        return 0
    normalized_keywords = [keyword.lower() for keyword in keywords if keyword]
    count = 0
    for doc in docs:
        haystack = _document_match_text(doc).lower()
        if any(keyword in haystack for keyword in normalized_keywords):
            count += 1
    return count


def _document_match_text(doc: Any) -> str:
    parts = [
        getattr(doc, "title", ""),
        getattr(doc, "content_preview", ""),
        " ".join(getattr(doc, "scene_tags", []) or []),
    ]
    return " ".join(str(part) for part in parts if part)


def _match_level(material_count: int) -> str:
    if material_count >= 10:
        return "高"
    if material_count >= 3:
        return "中"
    if material_count >= 1:
        return "低"
    return "无"
