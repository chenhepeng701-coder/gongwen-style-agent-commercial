"""Material card extractor — LLM-powered extraction of structured cards from documents."""

import json
import re
from datetime import datetime
from pathlib import Path

from app.config import get_settings
from app.models.library import LibraryDocument, MaterialCard
from app.services.library.indexer import get_document
from app.services.llm.factory import get_llm_provider
from app.utils.runtime_paths import get_library_cards_dir
from app.utils.storage import atomic_write_text


MAX_INPUT_CHARS = 8000  # Truncate document text to this length for LLM input
DEFAULT_CARD_COUNT = 3


def _cards_path(doc_id: str) -> Path:
    """Return path to the cards file for a document."""
    return get_library_cards_dir() / f"{doc_id}.json"


def _read_document_text(doc: LibraryDocument) -> str:
    """Read full text from the original document file."""
    file_path = Path(doc.file_path)
    if not file_path.exists():
        raise FileNotFoundError(f"原始文件不存在: {doc.file_path}。请检查文件是否被移动或删除。")

    ext = file_path.suffix.lower()
    if ext == ".docx":
        from app.services.parser.docx_parser import parse_docx_file
        result = parse_docx_file(str(file_path))
        return result["content"]
    else:
        # .txt / .md
        from app.services.parser.txt_parser import parse_text_file
        result = parse_text_file(str(file_path))
        return result["content"]


def _build_extraction_prompt(document_text: str, card_count: int = DEFAULT_CARD_COUNT) -> str:
    """Build the LLM prompt for card extraction."""
    prompt = f"""你是一位公文写作材料分析专家。请阅读以下公文文档，从中提取 {card_count} 个素材卡片。

每个素材卡片应包含以下 5 个字段：
1. **核心观点** (core_viewpoint): 文档中表达的核心论点或主张，1-2 句话精炼概括。
2. **论据支撑** (argument_support): 支持该观点的数据、案例、引用等论据，1-3 句话。
3. **典型表述** (typical_expression): 文档中具有代表性的表述方式、固定搭配或金句，1-3 个短语或句子。
4. **结构范式** (structure_pattern): 该段落的结构组织方式（如：现状-问题-对策、背景-意义-要求、总分总、并列递进等）。
5. **场景标签** (scene_tags): 适用场景关键词列表（如：["工作汇报", "政策解读", "动员讲话", "总结表彰"]）。

要求：
- 从文档中提取实质性内容，避免空洞概括
- 典型表述应直接引用或高度还原原文措辞
- 场景标签从公文常见场景中选择，每卡 2-4 个标签

请严格按以下 JSON 格式返回，不要包含任何其他文字：

```json
{{
  "cards": [
    {{
      "core_viewpoint": "...",
      "argument_support": "...",
      "typical_expression": "...",
      "structure_pattern": "...",
      "scene_tags": ["...", "..."]
    }}
  ]
}}
```

以下是公文文档内容：
---
{document_text}
---"""
    return prompt


def _parse_llm_response(response_text: str) -> list[dict]:
    """Parse the LLM JSON response, handling various formats."""
    # Try to extract JSON from markdown code blocks first
    json_match = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', response_text, re.DOTALL)
    if json_match:
        json_str = json_match.group(1).strip()
    else:
        # Try to find a JSON object directly
        json_match = re.search(r'\{[\s\S]*"cards"[\s\S]*\}', response_text)
        if json_match:
            json_str = json_match.group(0).strip()
        else:
            json_str = response_text.strip()

    try:
        data = json.loads(json_str)
    except json.JSONDecodeError:
        raise ValueError(f"LLM 返回的内容无法解析为 JSON。原始响应前 500 字符: {response_text[:500]}")

    cards = data.get("cards", [])
    if not cards:
        raise ValueError("LLM 返回的 JSON 中 'cards' 列表为空。")

    if not isinstance(cards, list):
        raise ValueError(f"LLM 返回的 'cards' 字段应为列表，实际类型: {type(cards).__name__}")

    return cards


def extract_cards(doc_id: str, card_count: int = None) -> dict:
    """Extract material cards from a document using LLM.

    Args:
        doc_id: The document ID to extract cards from.
        card_count: Number of cards to request (uses generation config if not set).

    Returns:
        dict with success, message, cards, card_count, model fields.
    """
    # Get document from index
    doc = get_document(doc_id)
    if doc is None:
        raise ValueError(f"文档不存在: {doc_id}")

    # Determine card count
    if card_count is None:
        settings = get_settings()
        card_count = settings.generation.subtitle_count

    # Read full document text
    full_text = _read_document_text(doc)
    if not full_text.strip():
        raise ValueError("文档内容为空，无法提取素材卡片。")

    # Truncate if too long
    input_text = full_text[:MAX_INPUT_CHARS]
    if len(full_text) > MAX_INPUT_CHARS:
        input_text += "\n\n[文档内容已截断，仅显示前 {} 字]".format(MAX_INPUT_CHARS)

    # Get LLM provider
    provider = get_llm_provider()

    # Build prompt and call LLM
    prompt = _build_extraction_prompt(input_text, card_count)
    system_prompt = "你是一位专业的公文写作材料分析专家。你只输出 JSON，不输出任何其他内容。"

    try:
        response_text = provider.generate_text(
            prompt=prompt,
            system_prompt=system_prompt,
            temperature=0.3,  # Lower temperature for structured extraction
        )
    except Exception as e:
        raise ValueError(f"LLM 调用失败: {e}")

    # Parse response
    try:
        raw_cards = _parse_llm_response(response_text)
    except Exception as e:
        raise ValueError(f"解析 LLM 响应失败: {e}")

    # Build MaterialCard objects
    cards = []
    for i, raw in enumerate(raw_cards):
        card = MaterialCard(
            card_index=i,
            core_viewpoint=str(raw.get("core_viewpoint", "") or "").strip(),
            argument_support=str(raw.get("argument_support", "") or "").strip(),
            typical_expression=str(raw.get("typical_expression", "") or "").strip(),
            structure_pattern=str(raw.get("structure_pattern", "") or "").strip(),
            scene_tags=[
                str(t).strip()
                for t in (raw.get("scene_tags", []) or [])
                if t and str(t).strip()
            ],
        )
        # Only include cards that have at least a core_viewpoint
        if card.core_viewpoint:
            cards.append(card)

    if not cards:
        raise ValueError("LLM 未提取到有效的素材卡片（所有卡片的核心观点均为空）。")

    # Save to file
    settings = get_settings()
    key = settings.current_provider or "deepseek"
    provider_cfg = settings.providers.get(key)
    model_used = provider_cfg.model if provider_cfg else "unknown"

    card_data = {
        "doc_id": doc_id,
        "extracted_at": datetime.now().isoformat(),
        "model": model_used,
        "card_count": len(cards),
        "cards": [c.model_dump() for c in cards],
    }
    atomic_write_text(
        _cards_path(doc_id),
        json.dumps(card_data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    return {
        "success": True,
        "message": f"素材卡片提取完成: {len(cards)} 张",
        "cards": cards,
        "card_count": len(cards),
        "model": model_used,
    }


def get_cards_info(doc_id: str) -> dict | None:
    """Get card extraction info for a document."""
    p = _cards_path(doc_id)
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def delete_cards(doc_id: str) -> bool:
    """Delete card file for a document."""
    p = _cards_path(doc_id)
    if not p.exists():
        return False
    p.unlink()
    return True
