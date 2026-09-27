"""Generation API — article generation endpoint and Word export."""

import json
import logging
import os
import subprocess
import sys
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Body, HTTPException
from fastapi.responses import FileResponse, Response

from app.models.generation import (
    ArticleGenRequest,
    ArticleGenBatchResult,
    GenerationArticleSnapshot,
    GenerationLogEntry,
    GenerationLogResponse,
    DocumentModeWordExportRequest,
    WordExportRequest,
    FullArticleSaveRequest,
    ArticleGenResult,
)
from app.services.generation.generator import (
    ArticleGenerator,
    FactSafetyRewriteConflictError,
    NoRelevantMaterialError,
    RetrievalInfraError,
    UserFactRetentionError,
)
from app.services.generation.article_traceability import build_article_source_traceability
from app.services.generation.flow3_v3_trace import AnalysisFactLeakError, UnsupportedFactError
from app.services.generation.planning_engine import INSUFFICIENT_EVIDENCE, PlanningFailure
from app.services.llm.openai_compatible_provider import LLMConnectionError, LLMInvalidResponseError
from app.services.export.word_exporter import clean_word_export_text, export_article_to_docx
from app.services.app_settings import (
    get_generation_defaults,
    get_output_dir,
    save_generation_defaults,
    save_output_dir,
)
from app.utils.file_utils import sanitize_title_filename
from app.utils.runtime_paths import get_logs_dir
from app.utils.storage import atomic_write_text
from app.utils.api_errors import internal_error

try:
    import httpx
except ImportError:  # pragma: no cover - httpx is a project dependency
    httpx = None

router = APIRouter()
logger = logging.getLogger(__name__)

ERROR_MESSAGES = {
    "MODEL_TIMEOUT": "AI模型响应超时，请稍后重试。",
    "MODEL_CONNECTION_ERROR": "AI模型连接失败，请检查模型配置。",
    "MODEL_INVALID_RESPONSE": "AI模型未返回有效正文，请重新生成。",
    "MATERIAL_INSUFFICIENT": "当前资料的真实事实容量不足，无法支持本次文章生成。请补充相关材料后重试。",
    "INSUFFICIENT_EVIDENCE": "当前资料不足以支撑安全生成，请补充资料或调整资料范围。",
    "DELIVERY_RESULT_UNAVAILABLE": "系统未能形成可交付文章，请重新生成。",
    "INTERNAL_ERROR": "服务器内部错误，请稍后重试。",
}


def _public_error_detail(
    error_code: str,
    message: str | None = None,
    *,
    original_error_type: str | None = None,
    **extra,
) -> dict:
    detail = {
        "success": False,
        "error_code": error_code,
        "error_type": error_code,
        "message": message or ERROR_MESSAGES.get(error_code, ERROR_MESSAGES["INTERNAL_ERROR"]),
    }
    if original_error_type:
        detail["original_error_type"] = original_error_type
    detail.update(extra)
    return detail


def _with_public_error_code(detail: dict, error_code: str, *, message: str | None = None) -> dict:
    original_error_type = detail.get("error_type")
    updated = dict(detail)
    updated["success"] = False
    updated["error_code"] = error_code
    updated["original_error_type"] = original_error_type or error_code
    if message:
        updated["message"] = message
    return updated


def _is_timeout_error(exc: BaseException | None) -> bool:
    if exc is None:
        return False
    if httpx is not None and isinstance(exc, httpx.TimeoutException):
        return True
    return "timeout" in type(exc).__name__.lower() or "timed out" in str(exc).lower()


def _llm_error_detail(exc: LLMConnectionError) -> tuple[int, dict]:
    error_code = "MODEL_TIMEOUT" if _is_timeout_error(exc.__cause__) else "MODEL_CONNECTION_ERROR"
    return 502, _public_error_detail(
        error_code,
        original_error_type=type(exc.__cause__).__name__ if exc.__cause__ else type(exc).__name__,
        fact_safety_blocked=False,
    )


def _planning_failure_response(exc: PlanningFailure) -> HTTPException:
    if exc.code == INSUFFICIENT_EVIDENCE:
        return HTTPException(
            status_code=422,
            detail=_public_error_detail(
                INSUFFICIENT_EVIDENCE,
                stage="PLANNING",
                retryable=True,
                preserve_form=True,
                fact_safety_blocked=False,
            ),
        )
    return HTTPException(
        status_code=500,
        detail=_public_error_detail(
            "INTERNAL_ERROR",
            original_error_type=exc.code,
            fact_safety_blocked=False,
        ),
    )

_generator = ArticleGenerator()
OFFICIAL_DRAFT_ONLY_MESSAGE = "当前版本仅支持 official_draft 公文初稿生成；旧生成模式已停用，请使用公文初稿模式重新生成。"
PUBLIC_HTTP_200_DELIVERY_STATES = frozenset({
    "SUCCESS",
    "SUCCESS_WITH_REPAIR",
    "SUCCESS_WITH_REVIEW",
})


def _map_delivery_result_to_http(batch: ArticleGenBatchResult) -> None:
    """Router-only mapping from the orchestrator's final product state."""

    if batch.delivery_status in PUBLIC_HTTP_200_DELIVERY_STATES:
        return
    if batch.delivery_status != "FAILED":
        raise HTTPException(
            status_code=500,
            detail=_public_error_detail(
                "INTERNAL_ERROR",
                original_error_type="UNKNOWN_DELIVERY_STATUS",
                fact_safety_blocked=False,
            ),
        )
    status_code = 400 if batch.failure_class == "REQUEST_FAILURE" else 500
    raise HTTPException(
        status_code=status_code,
        detail=_public_error_detail(
            "DELIVERY_RESULT_UNAVAILABLE",
            original_error_type=batch.failure_class,
            fact_safety_blocked=False,
        ),
    )


def _open_output_directory(path: Path) -> None:
    if os.name == "nt":
        os.startfile(str(path))  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.run(["open", str(path)], check=False)
    else:
        subprocess.run(["xdg-open", str(path)], check=False)


@router.get("/output-dir")
async def get_generation_output_dir():
    return {"output_dir": str(get_output_dir())}


@router.post("/open-output-dir")
async def open_generation_output_dir(payload: dict = Body(...)):
    raw_output_dir = payload.get("output_dir")
    if raw_output_dir is None or not str(raw_output_dir).strip():
        return {"success": False, "message": "输出目录不能为空"}

    output_path = Path(str(raw_output_dir).strip()).expanduser()
    try:
        if output_path.exists() and not output_path.is_dir():
            return {"success": False, "message": "当前路径不是文件夹"}

        output_path.mkdir(parents=True, exist_ok=True)
        if not output_path.is_dir():
            return {"success": False, "message": "当前路径不是文件夹"}

        _open_output_directory(output_path.resolve())
    except OSError as exc:
        logger.exception("GENERATION_OPEN_OUTPUT_DIR_ERROR")
        return {"success": False, "message": "输出目录无法打开，请检查路径权限。"}

    return {"success": True}


@router.post("/output-dir")
async def save_generation_output_dir(payload: dict = Body(...)):
    try:
        output_dir = save_output_dir(payload.get("output_dir", ""))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"success": True, "output_dir": str(output_dir)}


@router.get("/default-settings")
async def get_generation_default_settings():
    return get_generation_defaults()


@router.post("/default-settings")
async def save_generation_default_settings(payload: dict = Body(...)):
    try:
        settings = save_generation_defaults(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"success": True, "settings": settings}


def _log_path() -> Path:
    return get_logs_dir() / "generation_log.json"


def _legacy_log_path() -> Path:
    return get_output_dir() / "generation_log.json"

_log_lock = threading.Lock()

MAX_LOG_ENTRIES = 200


@dataclass(frozen=True)
class GenerationHistoryError(RuntimeError):
    path: Path
    reason: str

    def __str__(self) -> str:
        return f"Generation history is not readable ({self.reason}): {self.path}"


def _history_error_response(exc: GenerationHistoryError) -> HTTPException:
    return HTTPException(
        status_code=409,
        detail={
            "error_type": "GENERATION_HISTORY_CORRUPTED",
            "message": "Generation history is corrupted or has an invalid format; manual recovery is required.",
            "path": str(exc.path),
            "reason": exc.reason,
        },
    )


def _active_log_path() -> Path:
    p = _log_path()
    if p.exists():
        return p
    legacy = _legacy_log_path()
    if legacy.exists():
        return legacy
    return p


def _read_log() -> list[dict]:
    p = _active_log_path()
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise GenerationHistoryError(p, "invalid_json") from exc
    except UnicodeDecodeError as exc:
        raise GenerationHistoryError(p, "decode_error") from exc
    except OSError as exc:
        raise GenerationHistoryError(p, "read_error") from exc

    if not isinstance(data, list):
        raise GenerationHistoryError(p, "root_not_list")
    if any(not isinstance(entry, dict) for entry in data):
        raise GenerationHistoryError(p, "entry_not_object")
    return data


def _append_log(entry: GenerationLogEntry):
    with _log_lock:
        log = _read_log()
        log.append(entry.model_dump())
        # Trim oldest entries if over the limit
        if len(log) > MAX_LOG_ENTRIES:
            log = log[-MAX_LOG_ENTRIES:]
        atomic_write_text(
            _log_path(),
            json.dumps(log, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


def _write_log(entries: list[dict]):
    _read_log()
    atomic_write_text(
        _log_path(),
        json.dumps(entries, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _get_log_entry(entry_id: str) -> dict:
    try:
        entries = _read_log()
    except GenerationHistoryError as exc:
        raise _history_error_response(exc)
    for entry in entries:
        if entry.get("id") == entry_id:
            return entry
    raise HTTPException(status_code=404, detail="生成记录不存在")


def _iter_word_candidates(entry: dict) -> list[Path]:
    candidates: list[Path] = []

    entry_docx_path = entry.get("docx_path") or ""
    if entry_docx_path:
        candidates.append(Path(entry_docx_path))

    for file_path in entry.get("files") or []:
        path = Path(file_path)
        candidates.append(path)
        if path.suffix.lower() == ".txt":
            candidates.append(path.with_suffix(".docx"))

    for article in entry.get("articles") or []:
        article_docx_path = article.get("docx_path") or ""
        if article_docx_path:
            candidates.append(Path(article_docx_path))

        save_path = article.get("save_path") or ""
        if save_path:
            path = Path(save_path)
            candidates.append(path)
            if path.suffix.lower() == ".txt":
                candidates.append(path.with_suffix(".docx"))

        article_title = (article.get("title") or "").strip()
        if article_title:
            candidates.append(
                get_output_dir()
                / f"{sanitize_title_filename(article_title, '未命名文章')}.docx"
            )

    entry_title = (entry.get("title") or entry.get("request_title") or "").strip()
    if entry_title:
        candidates.append(
            get_output_dir() / f"{sanitize_title_filename(entry_title, '未命名文章')}.docx"
        )

    unique_candidates: list[Path] = []
    seen: set[str] = set()
    for path in candidates:
        normalized = str(path.resolve()) if path.is_absolute() else str(path)
        if normalized in seen:
            continue
        seen.add(normalized)
        unique_candidates.append(path)
    return unique_candidates


def _resolve_existing_word_path(entry: dict) -> Path:
    for candidate in _iter_word_candidates(entry):
        if candidate.suffix.lower() == ".docx" and candidate.exists():
            return candidate
    raise HTTPException(status_code=404, detail="文件不存在，可能已被移动或删除")


def _iter_file_candidates(entry: dict) -> list[Path]:
    candidates: list[Path] = []

    entry_docx_path = entry.get("docx_path") or ""
    if entry_docx_path:
        candidates.append(Path(entry_docx_path))

    for file_path in entry.get("files") or []:
        if file_path:
            candidates.append(Path(file_path))

    for article in entry.get("articles") or []:
        save_path = article.get("save_path") or ""
        if save_path:
            candidates.append(Path(save_path))
        article_docx_path = article.get("docx_path") or ""
        if article_docx_path:
            candidates.append(Path(article_docx_path))

    unique_candidates: list[Path] = []
    seen: set[str] = set()
    for path in candidates:
        normalized = str(path.resolve()) if path.is_absolute() else str(path)
        if normalized in seen:
            continue
        seen.add(normalized)
        unique_candidates.append(path)
    return unique_candidates


def _resolve_existing_output_path(entry: dict) -> Path:
    export_format = (entry.get("export_format") or "docx").strip().lower()
    expected_suffix = ".txt" if export_format == "txt" else ".docx"
    for candidate in _iter_file_candidates(entry):
        if candidate.suffix.lower() == expected_suffix and candidate.exists():
            return candidate
    if export_format == "docx":
        return _resolve_existing_word_path(entry)
    raise HTTPException(status_code=404, detail="文件不存在，可能已被移动或删除")


_EMPTY_EXPORT_SENTINELS = {"undefined", "null", "[]", "{}"}


def _unique_export_path(output_dir: Path, stem: str, suffix: str = ".docx") -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    for _ in range(20):
        candidate = (output_dir / f"{stem}_{uuid.uuid4().hex[:8]}{suffix}").resolve()
        if not candidate.exists():
            return candidate
    raise HTTPException(status_code=409, detail="Unable to allocate a unique export path")


def _resolve_explicit_export_path(raw_path: str) -> Path:
    output_path = Path(str(raw_path).strip()).expanduser().resolve()
    if output_path.exists():
        raise HTTPException(status_code=409, detail="Output path already exists; overwrite is not allowed")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    return output_path


def _normalize_publish_section_content(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        lines = []
        for index, item in enumerate(value, start=1):
            item_text = clean_word_export_text(str(item))
            if not item_text or item_text in _EMPTY_EXPORT_SENTINELS:
                continue
            lines.append(f"{index}. {item_text}")
        return lines
    if isinstance(value, dict):
        return []

    text = str(value)
    if text.strip() in _EMPTY_EXPORT_SENTINELS or not text.strip():
        return []
    return [line.strip() for line in clean_word_export_text(text).splitlines() if line.strip()]


def _export_document_mode_docx(payload: DocumentModeWordExportRequest, output_path: Path) -> str:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.exists():
        raise HTTPException(status_code=409, detail="Output path already exists; overwrite is not allowed")

    official_title = (payload.title or "").strip()
    official_body_lines: list[str] = []

    for section in payload.sections:
        label = (section.label or "").strip()
        lines = _normalize_publish_section_content(section.content)
        if not label or not lines:
            continue

        normalized_label = label.strip("：:")
        if normalized_label in {"标题", "正文标题"}:
            official_title = lines[0]
            continue

        if normalized_label == "正文内容":
            official_body_lines.extend(lines)

    official_body = "\n".join(official_body_lines).strip()
    if not official_title and not official_body:
        raise HTTPException(status_code=400, detail="Word导出内容不能为空")

    return export_article_to_docx(
        result={
            "recommended_title": official_title,
            "body": official_body,
            "summary": "",
            "risk_words": [],
        },
        cover_image_path="",
        structure_image_path="",
        output_path=str(output_path),
    )


def _feedback_field_log_payload(req: ArticleGenRequest) -> dict[str, str]:
    """v0.3 feedback fields, reserved for later RAG/prompt integration."""
    return {
        "document_type": req.document_type or "",
        "level": req.level or "",
        "org_type": req.org_type or "",
        "business_domain": req.business_domain or "",
        "writing_purpose": req.writing_purpose or "",
        "reference_scope": req.reference_scope or "",
    }


@router.get("/genres")
async def get_genre_catalog():
    """Registry-derived read-only writing options and production eligibility."""
    from app.services.generation.genres.catalog import genre_catalog

    return genre_catalog()


@router.get("/business-domains")
async def get_business_domain_catalog():
    """Read-only task context names; no evidence or production authority."""
    from app.services.business_domain_catalog import business_domain_catalog

    return business_domain_catalog()


@router.post("/generate", response_model=ArticleGenBatchResult)
async def generate_article(req: ArticleGenRequest):
    """Generate one or more articles from title/theme.

    count > 1 generates multiple articles with slight variations.
    """
    if not req.title.strip():
        raise HTTPException(status_code=400, detail="文章标题不能为空")

    effective_article_mode = (req.article_mode or "").strip().lower()
    if effective_article_mode != "official_draft":
        raise HTTPException(status_code=400, detail=OFFICIAL_DRAFT_ONLY_MESSAGE)
    from app.services.retrieval.scene_classifier import normalize_generation_scene
    from app.services.business_domain_catalog import normalize_business_domain

    req.business_domain = normalize_business_domain(req.business_domain)

    try:
        req.scene = normalize_generation_scene(req.scene)
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail=_public_error_detail("INVALID_SCENE", str(exc), field="scene"),
        ) from exc
    if (
        effective_article_mode == "official_draft"
        and (req.document_type or "").strip() == "党课讲稿"
        and (req.writing_purpose or "").strip() == "情况汇报"
    ):
        raise HTTPException(
            status_code=400,
            detail="党课讲稿不适合选择‘情况汇报’，请改为‘学习研讨’后再生成。",
        )

    try:
        logger.info(
            "GENERATION_REQUEST title=%s scene=%s style=%s article_mode=%s target_words=%s temperature=%s count=%s export_format=%s appreciation_scope=%s appreciation_count=%s",
            req.title,
            req.scene,
            req.style,
            req.article_mode,
            req.target_words,
            req.temperature,
            req.count,
            req.export_format,
            req.appreciation_scope,
            req.appreciation_count,
        )
        batch = await _generator.generate_commercial_flow(req)
        _map_delivery_result_to_http(batch)
        # All new response/history metadata uses the same normalized request;
        # legacy result adapters must not turn an empty Scene into a genre.
        for article in batch.articles:
            article.scene = req.scene or ""
        log_id = str(uuid.uuid4())[:8]

        first = batch.articles[0] if batch.articles else None

        entry = GenerationLogEntry(
            id=log_id,
            request_title=req.title,
            public_title=first.public_title if first else "",
            appreciation=first.appreciation if first else {},
            draft_full_article=first.draft_full_article if first else {},
            analysis_wrapper_enabled=first.analysis_wrapper_enabled if first else False,
            writing_tips=first.writing_tips if first else "",
            applicable_scenes=first.applicable_scenes if first else [],
            learning_points=first.learning_points if first else [],
            review_notes=first.review_notes if first else [],
            wrapped_preview=first.wrapped_preview if first else "",
            title=first.title if first else "",
            article_mode=req.article_mode,
            target_words=req.target_words,
            scene=first.scene if first else "",
            word_count=sum(a.word_count for a in batch.articles),
            status=(
                first.delivery_status
                if first and first.delivery_status in {
                    "QUALITY_TEST_DRAFT",
                    "REVIEW_REQUIRED_DRAFT",
                    "SUCCESS_WITH_REVIEW",
                }
                else "success"
            ),
            generation_time=round(
                sum(a.generation_time for a in batch.articles) / max(len(batch.articles), 1), 2
            ),
            count=batch.count,
            export_format=first.export_format if first else req.export_format,
            appreciation_scope=req.appreciation_scope,
            appreciation_count=req.appreciation_count,
            **_feedback_field_log_payload(req),
            timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            docx_path=first.docx_path if first else "",
            files=[a.save_path for a in batch.articles if a.save_path],
            articles=[
                GenerationArticleSnapshot(
                    article_mode=a.article_mode,
                    generation_route=a.generation_route,
                    generation_provenance=a.generation_provenance,
                    public_title=a.public_title,
                    draft_full_article=a.draft_full_article,
                    appreciation=a.appreciation,
                    analysis_wrapper_enabled=a.analysis_wrapper_enabled,
                    writing_tips=a.writing_tips,
                    applicable_scenes=a.applicable_scenes,
                    learning_points=a.learning_points,
                    review_notes=a.review_notes,
                    wrapped_preview=a.wrapped_preview,
                    title=a.title,
                    content=a.content,
                    article_markdown=a.article_markdown,
                    article_version=a.article_version,
                    article_sha256=a.article_sha256,
                    article_revision_id=a.article_revision_id,
                    parent_revision_id=a.parent_revision_id,
                    source_candidate_digest=a.source_candidate_digest,
                    previous_revision=a.previous_revision,
                    content_projection=a.content_projection,
                    full_article_check_status=a.full_article_check_status,
                    scene=a.scene,
                    retrieved_count=a.retrieved_count,
                    save_path=a.save_path,
                    docx_path=a.docx_path,
                    export_format=a.export_format,
                    appreciation_scope=a.appreciation_scope,
                    appreciation_count=a.appreciation_count,
                    reference_sources=a.reference_sources,
                    word_count=a.word_count,
                    fallback_used=a.fallback_used,
                    fallback_reason=a.fallback_reason,
                    selected_candidate_stage=a.selected_candidate_stage,
                    final_pipeline_stage_completed=a.final_pipeline_stage_completed,
                    fact_safety_rewrite_used=a.fact_safety_rewrite_used,
                    user_confirmation_required=a.user_confirmation_required,
                    unverified_claim_count=a.unverified_claim_count,
                    unverified_claim_types=a.unverified_claim_types,
                    quality_test_mode=a.quality_test_mode,
                    delivery_status=a.delivery_status,
                    official_success=a.official_success,
                    target_word_count=a.target_word_count,
                    fact_risk_count=a.fact_risk_count,
                    analysis_risk_count=a.analysis_risk_count,
                    fact_gap_count=a.fact_gap_count,
                    length_gate_status=a.length_gate_status,
                    structure_gate_status=a.structure_gate_status,
                    risk_items=a.risk_items,
                    article_exists=a.article_exists,
                    article_recoverable=a.article_recoverable,
                    article_qualified=a.article_qualified,
                    delivery_warnings=a.delivery_warnings,
                    failure_class=a.failure_class,
                    failure_stage=a.failure_stage,
                    reason=a.reason,
                    next_step=a.next_step,
                    review_suggestion=a.review_suggestion,
                    delivery_decision=a.delivery_decision,
                )
                for a in batch.articles
            ],
        )
        _append_log(entry)
        for article in batch.articles:
            article.log_id = log_id
        return batch
    except GenerationHistoryError as e:
        raise _history_error_response(e)
    except RetrievalInfraError as e:
        logger.exception("GENERATION_RETRIEVAL_INFRA_ERROR")
        _append_log(
            GenerationLogEntry(
                id=str(uuid.uuid4())[:8],
                request_title=req.title,
                title=req.title,
                article_mode=req.article_mode,
                target_words=req.target_words,
                scene=req.scene or "",
                word_count=0,
                status="failed",
                error_message=str(e),
                generation_time=0.0,
                count=req.count,
                export_format=req.export_format,
                appreciation_scope=req.appreciation_scope,
                appreciation_count=req.appreciation_count,
                **_feedback_field_log_payload(req),
                timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                files=[],
                articles=[],
            )
        )
        raise HTTPException(status_code=502, detail=e.to_response_detail())
    except NoRelevantMaterialError as e:
        _append_log(
            GenerationLogEntry(
                id=str(uuid.uuid4())[:8],
                request_title=req.title,
                title=req.title,
                article_mode=req.article_mode,
                target_words=req.target_words,
                scene=req.scene or "",
                word_count=0,
                status="failed",
                error_message=str(e),
                generation_time=0.0,
                count=req.count,
                export_format=req.export_format,
                appreciation_scope=req.appreciation_scope,
                appreciation_count=req.appreciation_count,
                **_feedback_field_log_payload(req),
                timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                files=[],
                articles=[],
            )
        )
        detail = _with_public_error_code(e.to_response_detail(), "MATERIAL_INSUFFICIENT")
        if req.article_mode == "document_mode":
            detail["title"] = "资料库素材不足，无法按文种生成"
            detail["message"] = "资料库素材不足，无法按文种生成。请先补充相关主题资料。"
        raise HTTPException(status_code=400, detail=detail)
    except LLMConnectionError as e:
        _append_log(
            GenerationLogEntry(
                id=str(uuid.uuid4())[:8],
                request_title=req.title,
                title=req.title,
                article_mode=req.article_mode,
                target_words=req.target_words,
                scene=req.scene or "",
                word_count=0,
                status="failed",
                error_message=str(e),
                generation_time=0.0,
                count=req.count,
                export_format=req.export_format,
                appreciation_scope=req.appreciation_scope,
                appreciation_count=req.appreciation_count,
                **_feedback_field_log_payload(req),
                timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                files=[],
                articles=[],
            )
        )
        status_code, detail = _llm_error_detail(e)
        raise HTTPException(status_code=status_code, detail=detail)
    except LLMInvalidResponseError as e:
        _append_log(
            GenerationLogEntry(
                id=str(uuid.uuid4())[:8],
                request_title=req.title,
                title=req.title,
                article_mode=req.article_mode,
                target_words=req.target_words,
                scene=req.scene or "",
                word_count=0,
                status="failed",
                error_message=str(e),
                generation_time=0.0,
                count=req.count,
                export_format=req.export_format,
                appreciation_scope=req.appreciation_scope,
                appreciation_count=req.appreciation_count,
                **_feedback_field_log_payload(req),
                timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                files=[],
                articles=[],
            )
        )
        raise HTTPException(
            status_code=502,
            detail=_public_error_detail(
                "MODEL_INVALID_RESPONSE",
                original_error_type=type(e).__name__,
                fact_safety_blocked=False,
            ),
        )
    except FactSafetyRewriteConflictError as e:
        _append_log(
            GenerationLogEntry(
                id=str(uuid.uuid4())[:8],
                request_title=req.title,
                title=req.title,
                article_mode=req.article_mode,
                target_words=req.target_words,
                scene=req.scene or "",
                word_count=0,
                status="failed",
                error_message=str(e),
                generation_time=0.0,
                count=req.count,
                export_format=req.export_format,
                appreciation_scope=req.appreciation_scope,
                appreciation_count=req.appreciation_count,
                **_feedback_field_log_payload(req),
                timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                files=[],
                articles=[],
            )
        )
        raise HTTPException(
            status_code=500,
            detail=_with_public_error_code(
                e.to_response_detail(),
                "DELIVERY_RESULT_UNAVAILABLE",
                message=ERROR_MESSAGES["DELIVERY_RESULT_UNAVAILABLE"],
            ),
        )
    except UserFactRetentionError as e:
        _append_log(
            GenerationLogEntry(
                id=str(uuid.uuid4())[:8],
                request_title=req.title,
                title=req.title,
                article_mode=req.article_mode,
                target_words=req.target_words,
                scene=req.scene or "",
                word_count=0,
                status="failed",
                error_message=str(e),
                generation_time=0.0,
                count=req.count,
                export_format=req.export_format,
                appreciation_scope=req.appreciation_scope,
                appreciation_count=req.appreciation_count,
                **_feedback_field_log_payload(req),
                timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                files=[],
                articles=[],
            )
        )
        raise HTTPException(
            status_code=500,
            detail=_with_public_error_code(
                e.to_response_detail(),
                "DELIVERY_RESULT_UNAVAILABLE",
                message=ERROR_MESSAGES["DELIVERY_RESULT_UNAVAILABLE"],
            ),
        )
    except (UnsupportedFactError, AnalysisFactLeakError) as e:
        _append_log(
            GenerationLogEntry(
                id=str(uuid.uuid4())[:8],
                request_title=req.title,
                title=req.title,
                article_mode=req.article_mode,
                target_words=req.target_words,
                scene=req.scene or "",
                word_count=0,
                status="failed",
                error_message=str(e),
                generation_time=0.0,
                count=req.count,
                export_format=req.export_format,
                appreciation_scope=req.appreciation_scope,
                appreciation_count=req.appreciation_count,
                **_feedback_field_log_payload(req),
                timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                files=[],
                articles=[],
            )
        )
        raise HTTPException(
            status_code=500,
            detail=_public_error_detail(
                "DELIVERY_RESULT_UNAVAILABLE",
                original_error_type=getattr(e, "flow3_v3_status", type(e).__name__),
                fact_safety_blocked=False,
            ),
        )
    except PlanningFailure as e:
        if e.code == INSUFFICIENT_EVIDENCE:
            logger.info("GENERATION_PLANNING_INSUFFICIENT_EVIDENCE")
        else:
            logger.exception("GENERATION_PLANNING_INTERNAL_ERROR")
        raise _planning_failure_response(e)
    except ValueError as e:
        _append_log(
            GenerationLogEntry(
                id=str(uuid.uuid4())[:8],
                request_title=req.title,
                title=req.title,
                article_mode=req.article_mode,
                target_words=req.target_words,
                scene=req.scene or "",
                word_count=0,
                status="failed",
                error_message=str(e),
                generation_time=0.0,
                count=req.count,
                export_format=req.export_format,
                appreciation_scope=req.appreciation_scope,
                appreciation_count=req.appreciation_count,
                **_feedback_field_log_payload(req),
                timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                files=[],
                articles=[],
            )
        )
        if req.generation_route == "full_article_drafting":
            raise HTTPException(status_code=400, detail=_public_error_detail(
                "DELIVERY_RESULT_UNAVAILABLE", original_error_type=type(e).__name__,
                generation_route=req.generation_route,
                contract_error_code=getattr(e, "code", "FULL_DRAFT_INPUT_OR_OUTPUT_INVALID"),
                fallback_used=False,
            ))
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("GENERATION_INTERNAL_ERROR")
        _append_log(
            GenerationLogEntry(
                id=str(uuid.uuid4())[:8],
                request_title=req.title,
                title=req.title,
                article_mode=req.article_mode,
                target_words=req.target_words,
                scene=req.scene or "",
                word_count=0,
                status="failed",
                error_message=str(e),
                generation_time=0.0,
                count=req.count,
                export_format=req.export_format,
                appreciation_scope=req.appreciation_scope,
                appreciation_count=req.appreciation_count,
                **_feedback_field_log_payload(req),
                timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                files=[],
                articles=[],
            )
        )
        raise HTTPException(
            status_code=500,
            detail=_public_error_detail(
                "INTERNAL_ERROR",
                original_error_type=type(e).__name__,
                fact_safety_blocked=False,
            ),
        )


@router.get("/log", response_model=GenerationLogResponse)
async def get_generation_log():
    """Get generation history log."""
    from app.services.generation.full_article_revision import history_entry_view
    try:
        entries = _read_log()
    except GenerationHistoryError as exc:
        raise _history_error_response(exc)
    entries.reverse()  # newest first
    entries = [history_entry_view(entry) for entry in entries]
    return GenerationLogResponse(
        entries=[GenerationLogEntry(**e) for e in entries],
        total=len(entries),
        output_dir=str(get_output_dir()),
    )


@router.post("/log/article")
async def save_full_article(payload: FullArticleSaveRequest):
    """Save a received full article or its edit without invoking generation."""
    from app.services.generation.full_article_revision import revise_full_article, history_article_view
    try:
        base = payload.base_article
        # Reject invalid delivery flags as well as stale content/projection.
        ArticleGenResult.model_validate(base.model_dump())
        with _log_lock:
            entries = _read_log()
            entry = next((e for e in entries if e.get("id") == payload.entry_id), None)
            if payload.entry_id and entry is None:
                raise HTTPException(status_code=404, detail="生成记录不存在")
            if entry is not None:
                articles = entry.get("articles", [])
                if payload.article_index >= len(articles):
                    raise HTTPException(status_code=404, detail="历史文章不存在")
                stored = GenerationArticleSnapshot.model_validate(history_article_view(articles[payload.article_index]))
                if stored.article_revision_id != base.article_revision_id:
                    raise HTTPException(status_code=409, detail={"error_type": "ARTICLE_REVISION_CONFLICT",
                        "message": "历史文章已有新版本，请重新打开后再编辑。"})
                base = stored
            base = GenerationArticleSnapshot.model_validate(history_article_view(base.model_dump()))
            current = revise_full_article(base, payload.article_markdown)
            if entry is None:
                entry = GenerationLogEntry(id=uuid.uuid4().hex[:8], title=current.title,
                    request_title=current.title, status=current.delivery_status,
                    timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"), articles=[current]).model_dump()
                entries.append(entry)
            else:
                entry["articles"][payload.article_index] = current.model_dump()
            if payload.article_index == 0:
                entry.update(title=current.title, status=current.delivery_status, docx_path=current.docx_path)
            entry["word_count"] = sum(a.get("word_count", 0) for a in entry["articles"])
            entry["files"] = [a["save_path"] for a in entry["articles"] if a.get("save_path")]
            _write_log(entries)
        result = ArticleGenResult.model_validate(current.model_dump())
        result.log_id = entry["id"]
        return {"article": result, "log_id": entry["id"]}
    except GenerationHistoryError as exc:
        raise _history_error_response(exc)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail={"error_type": "ARTICLE_CONTRACT_INVALID",
            "message": str(exc)}) from exc


@router.post("/export/txt")
async def export_article_txt(payload: WordExportRequest):
    if payload.article_markdown is None:
        raise HTTPException(status_code=422, detail="全文 TXT 导出需要 article_markdown")
    return Response(content=payload.article_markdown.encode("utf-8"), media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="article.txt"'})


@router.get("/log/{entry_id}/source-traceability")
async def get_generation_source_traceability(entry_id: str):
    """Return saved article/source mappings without invoking retrieval or a model."""

    entry = _get_log_entry(entry_id)
    articles = entry.get("articles") or []
    if not articles or not isinstance(articles[0], dict):
        raise HTTPException(status_code=404, detail="这条生成记录没有可查看的文章正文。")
    return build_article_source_traceability(articles[0])


@router.delete("/log/{entry_id}")
async def delete_generation_log(entry_id: str):
    """Delete one generation log entry by id."""
    try:
        entries = _read_log()
    except GenerationHistoryError as exc:
        raise _history_error_response(exc)
    filtered = [entry for entry in entries if entry.get("id") != entry_id]
    if len(filtered) == len(entries):
        raise HTTPException(status_code=404, detail="生成记录不存在")
    _write_log(filtered)
    return {"success": True, "message": "已删除生成记录"}


@router.post("/log/{entry_id}/open-word")
async def open_generation_word(entry_id: str):
    """Open the generated Word file with the system default application."""
    entry = _get_log_entry(entry_id)
    word_path = _resolve_existing_word_path(entry)
    _open_output_directory(word_path)
    return {"success": True, "path": str(word_path)}


@router.post("/log/{entry_id}/open-file")
async def open_generation_file(entry_id: str):
    """Open the generated output file based on the export format."""
    entry = _get_log_entry(entry_id)
    file_path = _resolve_existing_output_path(entry)
    _open_output_directory(file_path)
    return {"success": True, "path": str(file_path)}


@router.post("/log/{entry_id}/open-folder")
async def open_generation_folder(entry_id: str):
    """Open Explorer and select the generated output file."""
    entry = _get_log_entry(entry_id)
    file_path = _resolve_existing_output_path(entry)
    if os.name == "nt":
        subprocess.Popen(["explorer.exe", f"/select,{file_path}"])
    elif sys.platform == "darwin":
        subprocess.Popen(["open", "-R", str(file_path)])
    else:
        _open_output_directory(file_path.parent)
    return {"success": True, "path": str(file_path.parent)}


@router.post("/export/word")
async def export_word(payload: WordExportRequest | None = Body(default=None), title: str = "", content: str = ""):
    """Export generated article (title + body) as a .docx file."""
    final_title = (payload.title if payload else title) or title
    final_content = (payload.content if payload else content) or content

    if not final_title.strip() and not final_content.strip():
        raise HTTPException(status_code=400, detail="标题和内容不能同时为空")

    safe_name = sanitize_title_filename(final_title, "未命名文章")
    requested_output_path = (payload.output_path if payload else "").strip()
    if requested_output_path:
        output_path = _resolve_explicit_export_path(requested_output_path)
    else:
        output_path = _unique_export_path(get_output_dir(), safe_name)

    result_dict = {
        "recommended_title": final_title,
        "body": final_content,
        "content_format": payload.content_format if payload else "plain",
        "summary": "",
        "risk_words": [],
    }

    try:
        file_path = export_article_to_docx(
            result=result_dict,
            cover_image_path="",
            structure_image_path="",
            output_path=str(output_path),
        )
    except Exception as e:
        raise internal_error(logger, "GENERATION_WORD_EXPORT_ERROR", e)

    filename = output_path.name
    return FileResponse(
        path=file_path,
        filename=filename,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )


@router.post("/export/wechat-word")
async def export_document_mode_word(payload: DocumentModeWordExportRequest):
    """Export document mode draft sections as a separate .docx file."""
    safe_title = sanitize_title_filename(payload.title, "未命名")
    output_path = _unique_export_path(get_output_dir(), f"Word导出_{safe_title}")
    filename = output_path.name

    try:
        file_path = _export_document_mode_docx(payload, output_path)
    except HTTPException:
        raise
    except Exception as e:
        raise internal_error(logger, "GENERATION_WECHAT_WORD_EXPORT_ERROR", e)

    return FileResponse(
        path=file_path,
        filename=filename,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
