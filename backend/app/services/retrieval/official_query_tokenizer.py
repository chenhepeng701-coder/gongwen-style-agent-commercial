"""Deterministic lightweight tokenizer for Chinese official-document retrieval."""

from __future__ import annotations

import re

TOKENIZER_VERSION = "official_query_tokenizer_v2"

_STOPWORDS = {
    "如何", "怎样", "怎么", "做到", "关于", "有关", "进行", "开展", "工作",
    "情况", "一个", "一些", "相关", "重点", "当前", "进一步", "不断",
}

_SEGMENT_RE = re.compile(
    r"\d+(?:\.\d+)?[%％]"
    r"|\d+(?:[+＋\-×xX/]\d+)+"
    r"|(?:19|20)\d{2}年?"
    r"|\d+(?:\.\d+)?(?:岁以下|次|年|月|日|季度|个|项|人|家|件|万元)?"
    r"|[A-Za-z][A-Za-z0-9]*(?:[-_.][A-Za-z0-9]+)*"
    r"|[\u4e00-\u9fff]+"
)
_MIXED_SPAN_RE = re.compile(r"[A-Za-z0-9_.+＋\-\u4e00-\u9fff]+")


def normalize_query(text: str) -> str:
    """Collapse punctuation and whitespace without changing meaningful text."""
    return " ".join(_SEGMENT_RE.findall((text or "").lower()))


def tokenize_official_query(text: str, *, include_trigrams: bool = True) -> list[str]:
    """Return stable terms: phrases, latin/numbers, and Chinese 2/3-grams."""
    normalized = normalize_query(text)
    if not normalized:
        return []

    tokens: list[str] = []
    for span in _MIXED_SPAN_RE.findall((text or "").lower()):
        if len(span) <= 64 and re.search(r"[a-z0-9]", span) and re.search(r"[\u4e00-\u9fff]", span):
            tokens.append(span)
    for segment in normalized.split():
        if segment in _STOPWORDS:
            continue
        if not re.fullmatch(r"[\u4e00-\u9fff]+", segment):
            tokens.append(segment)
            continue
        # Preserve complete names and policy phrases without a query-specific
        # dictionary; 2/3-grams still match them when embedded in longer text.
        if len(segment) <= 64:
            tokens.append(segment)
        for width in ((2, 3) if include_trigrams else (2,)):
            if len(segment) < width:
                continue
            tokens.extend(segment[index:index + width] for index in range(len(segment) - width + 1))

    return [token for token in tokens if token and token not in _STOPWORDS]
