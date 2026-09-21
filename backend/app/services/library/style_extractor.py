"""Style card extractor — LLM-powered extraction of stylistic features from documents."""

import json
import re
from datetime import datetime
from pathlib import Path

from app.config import get_settings
from app.models.library import LibraryDocument, StyleCard
from app.services.library.indexer import get_document
from app.services.llm.factory import get_llm_provider
from app.utils.runtime_paths import get_library_styles_dir
from app.utils.storage import atomic_write_text

MAX_INPUT_CHARS = 8000
SYSTEM_PROMPT = "你是一位专业的公文写作风格分析专家。你只输出 JSON，不输出任何其他内容。"


def _styles_path(doc_id: str) -> Path:
    return get_library_styles_dir() / f"{doc_id}.json"


def _read_document_text(doc: LibraryDocument) -> str:
    file_path = Path(doc.file_path)
    if not file_path.exists():
        raise FileNotFoundError(f"原始文件不存在: {doc.file_path}。请检查文件是否被移动或删除。")

    ext = file_path.suffix.lower()
    if ext == ".docx":
        from app.services.parser.docx_parser import parse_docx_file
        result = parse_docx_file(str(file_path))
        return result["content"]
    else:
        from app.services.parser.txt_parser import parse_text_file
        result = parse_text_file(str(file_path))
        return result["content"]


def _build_style_extraction_prompt(document_text: str) -> str:
    prompt = f"""你是一位公文写作风格分析专家。请仔细阅读以下公文文档，提取其写作风格特征。

请从以下 6 个维度分析文档的风格特征：

1. **句式偏好** (sentence_pattern):
   - 描述句子长度的倾向（长句/短句/长短结合）
   - 句式结构特点（排比句、对仗句、递进复句、动宾连用等）
   - 给出 2-3 个典型句式示例（从原文摘取或归纳）

2. **用词习惯** (word_habit):
   - 高频出现的核心动词、副词搭配
   - 固定表达方式（如"以……为……"、"着力……"、"切实……"等）
   - 程度词和语气词的使用偏好
   - 列出 5-8 个高频特征词/短语

3. **开头范式** (opening_paradigm):
   - 识别开头的组织模式（形势分析型、背景交代型、重要意义阐述型、问题导向型等）
   - 描述开头段落的结构特征
   - 给出 1-2 个开头范式的简要概括

4. **结尾范式** (closing_paradigm):
   - 识别结尾的组织模式（号召动员型、表态收束型、总结升华型等）
   - 描述结尾段落的结构特征
   - 给出 1-2 个结尾范式的简要概括

5. **论证节奏** (argument_rhythm):
   - 描述段落的推进逻辑（层层递进/并列铺陈/正反对比/因果推演/总分总等）
   - 描述段落之间的衔接方式
   - 给出 1-2 个论证节奏的简要概括

6. **语气语调** (tone_register):
   - 整体语气的定位（庄重严肃/亲切务实/激昂动员/冷静分析/温情关怀等）
   - 描述人称使用习惯（第一人称/第二人称/第三人称偏好）
   - 描述情感色彩强度（强/中/弱）

请严格按以下 JSON 格式返回，不要包含任何其他文字：

```json
{{
  "sentence_pattern": {{
    "length_tendency": "...",
    "structure_features": "...",
    "typical_examples": ["...", "..."]
  }},
  "word_habit": {{
    "core_verbs": "...",
    "fixed_expressions": "...",
    "high_freq_terms": ["...", "...", "...", "...", "..."]
  }},
  "opening_paradigm": {{
    "pattern_type": "...",
    "structure_desc": "...",
    "examples_brief": ["..."]
  }},
  "closing_paradigm": {{
    "pattern_type": "...",
    "structure_desc": "...",
    "examples_brief": ["..."]
  }},
  "argument_rhythm": {{
    "progression_logic": "...",
    "transition_style": "...",
    "examples_brief": ["..."]
  }},
  "tone_register": {{
    "overall_tone": "...",
    "person_preference": "...",
    "emotional_intensity": "..."
  }}
}}
```

以下是公文文档内容：
---
{document_text}
---"""
    return prompt


def _parse_llm_response(response_text: str) -> dict:
    json_match = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', response_text, re.DOTALL)
    if json_match:
        json_str = json_match.group(1).strip()
    else:
        json_match = re.search(r'\{[\s\S]*\}', response_text)
        if json_match:
            json_str = json_match.group(0).strip()
        else:
            json_str = response_text.strip()

    try:
        data = json.loads(json_str)
    except json.JSONDecodeError:
        raise ValueError(f"LLM 返回的内容无法解析为 JSON。原始响应前 500 字符: {response_text[:500]}")

    # Validate required top-level fields
    required = ["sentence_pattern", "word_habit", "opening_paradigm", "closing_paradigm", "argument_rhythm", "tone_register"]
    missing = [f for f in required if f not in data]
    if missing:
        raise ValueError(f"LLM 返回的 JSON 缺少字段: {', '.join(missing)}")

    return data


def extract_style(doc_id: str) -> dict:
    """Extract style card from a document using LLM.

    Args:
        doc_id: The document ID to extract style from.

    Returns:
        dict with success, message, style_card, model fields.
    """
    doc = get_document(doc_id)
    if doc is None:
        raise ValueError(f"文档不存在: {doc_id}")

    full_text = _read_document_text(doc)
    if not full_text.strip():
        raise ValueError("文档内容为空，无法提取风格特征。")

    input_text = full_text[:MAX_INPUT_CHARS]
    if len(full_text) > MAX_INPUT_CHARS:
        input_text += f"\n\n[文档内容已截断，仅显示前 {MAX_INPUT_CHARS} 字]"

    provider = get_llm_provider()
    prompt = _build_style_extraction_prompt(input_text)

    try:
        response_text = provider.generate_text(
            prompt=prompt,
            system_prompt=SYSTEM_PROMPT,
            temperature=0.3,
        )
    except Exception as e:
        raise ValueError(f"LLM 调用失败: {e}")

    try:
        raw_style = _parse_llm_response(response_text)
    except Exception as e:
        raise ValueError(f"解析 LLM 响应失败: {e}")

    # Build StyleCard
    card = StyleCard(
        sentence_pattern=raw_style.get("sentence_pattern", {}),
        word_habit=raw_style.get("word_habit", {}),
        opening_paradigm=raw_style.get("opening_paradigm", {}),
        closing_paradigm=raw_style.get("closing_paradigm", {}),
        argument_rhythm=raw_style.get("argument_rhythm", {}),
        tone_register=raw_style.get("tone_register", {}),
    )

    # Save to file
    settings = get_settings()
    key = settings.current_provider or "deepseek"
    provider_cfg = settings.providers.get(key)
    model_used = provider_cfg.model if provider_cfg else "unknown"

    style_data = {
        "doc_id": doc_id,
        "extracted_at": datetime.now().isoformat(),
        "model": model_used,
        "style_card": card.model_dump(),
    }
    atomic_write_text(
        _styles_path(doc_id),
        json.dumps(style_data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    return {
        "success": True,
        "message": "风格卡片提取完成",
        "style_card": card,
        "model": model_used,
    }


def get_style_info(doc_id: str) -> dict | None:
    """Get style card info for a document."""
    p = _styles_path(doc_id)
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def delete_style(doc_id: str) -> bool:
    """Delete style card file for a document."""
    p = _styles_path(doc_id)
    if not p.exists():
        return False
    p.unlink()
    return True
