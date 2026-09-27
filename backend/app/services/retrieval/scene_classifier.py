"""Scene classifier — LLM-powered scene type recognition for documents and queries."""

import json
import re
from datetime import datetime
from pathlib import Path

from app.config import get_settings
from app.services.llm.factory import get_llm_provider
from app.services.library.indexer import get_document, list_documents, update_scene_tags
from app.utils.runtime_paths import get_library_index_dir

# Pre-defined scene taxonomy (can be extended via LLM)
SCENE_TAXONOMY = [
    {"name": "民主生活会对照检查", "keywords": ["民主生活会", "对照检查", "党性分析", "批评与自我批评"]},
    {"name": "年度工作总结", "keywords": ["年度总结", "工作总结", "年终总结", "全年工作"]},
    {"name": "述职报告", "keywords": ["述职", "履职", "述廉", "述职述廉"]},
    {"name": "专题汇报", "keywords": ["汇报", "专题报告", "情况汇报"]},
    {"name": "动员讲话", "keywords": ["动员", "讲话", "动员大会", "誓师"]},
    {"name": "调研报告", "keywords": ["调研", "调查", "考察报告", "调研报告"]},
    {"name": "党课讲稿", "keywords": ["党课", "党性教育", "党建工作", "学习教育"]},
    {"name": "形势分析", "keywords": ["形势", "分析", "态势", "研判"]},
    {"name": "工作计划", "keywords": ["计划", "规划", "工作要点", "安排"]},
    {"name": "整改方案", "keywords": ["整改", "措施", "方案", "治理"]},
    {"name": "情况通报", "keywords": ["通报", "公告", "通知"]},
    {"name": "经验交流", "keywords": ["经验", "交流", "分享", "做法"]},
    {"name": "先进事迹", "keywords": ["事迹", "先进", "典型", "榜样"]},
    {"name": "政治表态", "keywords": ["表态", "拥护", "贯彻", "落实"]},
    {"name": "组织生活会", "keywords": ["组织生活", "支部", "党员大会"]},
    {"name": "意见方案", "keywords": ["意见", "建议", "提案", "方案"]},
]

SYSTEM_PROMPT = "你是一位专业的公文场景分类专家。你只输出 JSON，不输出任何其他内容。"


def _get_scene_taxonomy_text() -> str:
    lines = []
    for i, s in enumerate(SCENE_TAXONOMY, 1):
        kw = "、".join(s["keywords"])
        lines.append(f"{i}. {s['name']}（关键词: {kw}）")
    return "\n".join(lines)


def classify_by_title_or_query(text: str) -> dict:
    """Classify a title/query into scene categories using LLM.

    Returns dict with:
        - primary_scene: str (top match)
        - secondary_scenes: list[str] (other possible scenes)
        - confidence: float (0-1)
        - reasoning: str (brief explanation)
    """
    taxonomy_text = _get_scene_taxonomy_text()
    prompt = f"""你是公文场景分类专家。请将以下标题/主题归类到最合适的公文场景。

可用场景列表：
{taxonomy_text}

标题/主题：{text}

请判断该标题最可能属于哪个场景，以及可能的次要场景，用 JSON 格式返回：

```json
{{
  "primary_scene": "最匹配的场景名称",
  "secondary_scenes": ["可能的次要场景1", "可能的次要场景2"],
  "confidence": 0.85,
  "reasoning": "简要判断理由（1-2句话）"
}}
```

注意：
- primary_scene 必须从上述场景列表中选择
- confidence 取值 0-1，表示分类确信度
- 如果标题可能跨多个场景，在 secondary_scenes 中列出"""

    try:
        provider = get_llm_provider()
        response_text = provider.generate_text(
            prompt=prompt,
            system_prompt=SYSTEM_PROMPT,
            temperature=0.2,
        )
        return _parse_scene_response(response_text)
    except Exception as e:
        raise ValueError(f"场景识别 LLM 调用失败: {e}")


def classify_document(doc_id: str) -> dict:
    """Auto-classify a document's scene and update its tags.

    Reads document title + content preview, classifies scene,
    then updates the document's scene_tags.
    """
    doc = get_document(doc_id)
    if doc is None:
        raise ValueError(f"文档不存在: {doc_id}")

    # Read document full text for better classification
    file_path = Path(doc.file_path)
    if not file_path.exists():
        raise FileNotFoundError(f"原始文件不存在: {doc.file_path}")

    ext = file_path.suffix.lower()
    if ext == ".docx":
        from app.services.parser.docx_parser import parse_docx_file
        result = parse_docx_file(str(file_path))
    else:
        from app.services.parser.txt_parser import parse_text_file
        result = parse_text_file(str(file_path))

    # Use title + first 2000 chars for classification
    content = result["content"]
    classification_text = f"标题：{doc.title}\n\n内容摘要：{content[:2000]}"

    result = classify_by_title_or_query(classification_text)

    # Update document scene tags
    new_tags = [result["primary_scene"]] + (result.get("secondary_scenes", []) or [])
    update_scene_tags(doc_id, new_tags)

    result["applied_tags"] = new_tags
    return result


def batch_classify_all() -> dict:
    """Run scene classification on all un-tagged documents."""
    docs = list_documents()["items"]
    untagged = [d for d in docs if not d.scene_tags]
    results = {"total": len(untagged), "classified": 0, "failed": 0, "details": []}

    for doc in untagged:
        try:
            r = classify_document(doc.id)
            results["classified"] += 1
            results["details"].append({
                "doc_id": doc.id,
                "title": doc.title,
                "primary_scene": r["primary_scene"],
            })
        except Exception as e:
            results["failed"] += 1
            results["details"].append({
                "doc_id": doc.id,
                "title": doc.title,
                "error": str(e),
            })

    return results


def get_scene_taxonomy() -> list[dict]:
    """Return the scene taxonomy list."""
    return SCENE_TAXONOMY


def normalize_generation_scene(value: str | None) -> str | None:
    """Validate new generation input, without classification or legacy guesses.

    The API applies this once before Retrieval/Task Assembly/Writer. Historical
    records remain readable; their genre-as-scene recovery is a separate path.
    """
    scene = (value or "").strip()
    if scene.lower().replace(" ", "") in {
        "", "auto", "自动", "未指定", "自动/未指定", "自动识别场景",
    }:
        return None
    if scene not in {item["name"] for item in get_scene_taxonomy()}:
        raise ValueError("请选择有效的写作场景，或选择自动 / 未指定。")
    return scene


def get_scene_stats() -> dict:
    """Get document counts per scene."""
    docs = list_documents()["items"]
    scene_counts = {}
    for doc in docs:
        for tag in doc.scene_tags:
            scene_counts[tag] = scene_counts.get(tag, 0) + 1
    return {
        "total_documents": len(docs),
        "tagged_documents": len([d for d in docs if d.scene_tags]),
        "scene_dist": dict(sorted(scene_counts.items(), key=lambda x: x[1], reverse=True)),
    }


def _parse_scene_response(response_text: str) -> dict:
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

    return {
        "primary_scene": data.get("primary_scene", "未分类"),
        "secondary_scenes": data.get("secondary_scenes", []) or [],
        "confidence": data.get("confidence", 0.0),
        "reasoning": data.get("reasoning", ""),
    }
