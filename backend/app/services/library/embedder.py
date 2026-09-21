"""Embedding generator: text chunking + embedding API + vector storage."""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

import httpx

from app.config import EmbeddingConfig, get_embedding_config as get_ec, get_settings
from app.models.library import EmbeddingChunk
from app.services.library.indexer import _index_path, get_document
from app.services.retrieval.official_semantic_chunker import semantic_chunk_document
from app.utils.http_client import resolve_http_client_settings
from app.utils.runtime_paths import get_library_embeddings_dir
from app.utils.storage import atomic_write_text

logger = logging.getLogger(__name__)

CHUNK_SIZE = 500
CHUNK_OVERLAP = 50
_DASHSCOPE_COMPAT_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode"


def _embedding_path(doc_id: str) -> Path:
    return get_library_embeddings_dir() / f"{doc_id}.json"


def _chunk_text(text: str, size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    chunks = []
    start = 0
    while start < len(text):
        end = min(start + size, len(text))
        chunk = text[start:end]
        if end < len(text):
            for sep in ["\n", "。", "！", "？", " "]:
                last = chunk.rfind(sep)
                if last > size // 2:
                    end = start + last + 1
                    chunk = text[start:end]
                    break
        chunks.append(chunk.strip())
        start = end - overlap if end < len(text) else end
        if start >= len(text):
            break
    return [chunk for chunk in chunks if chunk]


def _normalize_dashscope_base_url(base_url: str) -> str:
    normalized = (base_url or "").strip().rstrip("/")
    if not normalized:
        return normalized

    if "api.dashscope.aliyuncs.com" in normalized:
        return _DASHSCOPE_COMPAT_BASE_URL

    if "dashscope.aliyuncs.com" in normalized and "compatible-mode" not in normalized:
        return f"{normalized}/compatible-mode"

    return normalized


def _normalize_embedding_endpoint(base_url: str, endpoint_path: str) -> tuple[str, str]:
    normalized_base = _normalize_dashscope_base_url(base_url)
    normalized_endpoint = (endpoint_path or "/v1/embeddings").strip() or "/v1/embeddings"
    if not normalized_endpoint.startswith("/"):
        normalized_endpoint = f"/{normalized_endpoint}"
    return normalized_base, normalized_endpoint


def _get_embedding_config() -> tuple[EmbeddingConfig, str]:
    embedding_cfg = get_ec()

    if embedding_cfg.model and embedding_cfg.base_url and embedding_cfg.api_key:
        normalized_base, normalized_endpoint = _normalize_embedding_endpoint(
            embedding_cfg.base_url,
            embedding_cfg.endpoint_path,
        )
        return (
            embedding_cfg.model_copy(
                update={
                    "base_url": normalized_base,
                    "endpoint_path": normalized_endpoint,
                }
            ),
            embedding_cfg.model,
        )

    settings = get_settings()
    key = settings.current_provider or "deepseek"
    provider = settings.providers.get(key)
    if provider is None:
        raise ValueError(f"未配置服务商: {key}。请先在模型配置页配置 API Key。")
    if not provider.api_key:
        raise ValueError(f"服务商 {key} 未配置 API Key。请先在模型配置页填写 API Key。")

    model = provider.embedding_model
    if not model:
        raise ValueError(
            f"服务商 {key} 未配置 Embedding 模型。"
            "请在模型配置页为当前服务商设置 embedding_model，"
            "或在「向量模型」tab 配置独立的 Embedding 服务。"
        )

    normalized_base, normalized_endpoint = _normalize_embedding_endpoint(
        provider.base_url,
        "/v1/embeddings",
    )
    fallback = EmbeddingConfig(
        provider=provider.provider,
        api_key=provider.api_key,
        base_url=normalized_base,
        model=model,
        endpoint_path=normalized_endpoint,
        timeout=provider.timeout,
        headers=provider.headers,
    )
    return fallback, model


def _classify_embedding_error(exc: Exception) -> tuple[str, str]:
    if isinstance(exc, httpx.TimeoutException):
        return "timeout", "网络超时"

    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        if status == 401:
            return "api_key_failed", "API Key 无效或未授权"
        if status == 404:
            return "base_url_error", "Base URL 或 endpoint 不正确"
        if status == 400:
            body = ""
            try:
                body = exc.response.text.lower()
            except Exception:
                body = ""
            if "model" in body:
                return "model_not_found", "模型不存在或请求格式不兼容"
            return "bad_request", "请求参数错误"
        return "http_error", f"HTTP {status}"

    if isinstance(exc, httpx.RequestError):
        message = str(exc).lower()
        if "getaddrinfo failed" in message or "name or service not known" in message:
            return "dns_failure", "DNS 解析失败"
        if "connection refused" in message or "10061" in message:
            return "proxy_failure", "代理或目标连接失败"
        return "network_error", "网络请求失败"

    return "unknown", str(exc)


def _log_embedding_context(
    *,
    service_name: str,
    emb_cfg: EmbeddingConfig,
    model: str,
    proxy_mode: str,
    invalid_proxy_ignored: bool,
) -> None:
    logger.info("EMBEDDING_PROVIDER=%s", emb_cfg.provider)
    logger.info("EMBEDDING_MODEL=%s", model)
    logger.info("EMBEDDING_BASE_URL=%s", emb_cfg.base_url)
    logger.info("EMBEDDING_PROXY_MODE=%s", proxy_mode)
    logger.info("INVALID_PROXY_IGNORED=%s", str(invalid_proxy_ignored).lower())
    logger.debug("EMBEDDING_SERVICE=%s", service_name)


def _call_embedding_api(emb_cfg: EmbeddingConfig, model: str, texts: list[str]) -> list[list[float]]:
    base_url, endpoint = _normalize_embedding_endpoint(emb_cfg.base_url, emb_cfg.endpoint_path)
    url = f"{base_url}{endpoint}"

    client_settings = resolve_http_client_settings("EMBEDDING_HTTP")
    _log_embedding_context(
        service_name="EMBEDDING_HTTP",
        emb_cfg=emb_cfg.model_copy(update={"base_url": base_url, "endpoint_path": endpoint}),
        model=model,
        proxy_mode=client_settings.proxy_mode,
        invalid_proxy_ignored=client_settings.invalid_proxy_ignored,
    )

    headers = {
        "Authorization": f"Bearer {emb_cfg.api_key}",
        "Content-Type": "application/json",
    }
    headers.update({str(key): str(value) for key, value in emb_cfg.headers.items()})

    batch_size = 10
    all_vectors: list[list[float]] = []
    for index in range(0, len(texts), batch_size):
        batch = texts[index : index + batch_size]
        try:
            response = httpx.post(
                url,
                headers=headers,
                json={
                    "model": model,
                    "input": batch,
                },
                timeout=max(emb_cfg.timeout, 60),
                **client_settings.request_kwargs,
            )
            response.raise_for_status()
            payload = response.json()
            data_items = payload["data"]
            data_items.sort(key=lambda item: item.get("index", 0))
            batch_vectors = [item["embedding"] for item in data_items]
            all_vectors.extend(batch_vectors)
            if batch_vectors:
                logger.info("EMBEDDING_VECTOR_DIM=%s", len(batch_vectors[0]))
        except KeyError:
            logger.info("EMBEDDING_ERROR_TYPE=response_format_error")
            raise ValueError(f"Embedding 返回格式异常，缺少 'data' 字段: {payload}")
        except Exception as exc:
            error_type, _ = _classify_embedding_error(exc)
            logger.info("EMBEDDING_ERROR_TYPE=%s", error_type)
            raise

    return all_vectors


def test_embedding_connection(config: EmbeddingConfig) -> dict:
    if not config.api_key:
        logger.info("EMBEDDING_TEST_SUCCESS=false")
        return {
            "success": False,
            "message": "未配置 Embedding API Key。请先填写 API Key 再测试。",
            "response": "",
            "vector_dim": 0,
            "error_type": "missing_api_key",
        }
    if not config.base_url:
        logger.info("EMBEDDING_TEST_SUCCESS=false")
        return {
            "success": False,
            "message": "未配置 Embedding Base URL。",
            "response": "",
            "vector_dim": 0,
            "error_type": "missing_base_url",
        }
    if not config.model:
        logger.info("EMBEDDING_TEST_SUCCESS=false")
        return {
            "success": False,
            "message": "未配置 Embedding Model。",
            "response": "",
            "vector_dim": 0,
            "error_type": "missing_model",
        }

    normalized_base, normalized_endpoint = _normalize_embedding_endpoint(config.base_url, config.endpoint_path)
    normalized_config = config.model_copy(
        update={
            "base_url": normalized_base,
            "endpoint_path": normalized_endpoint,
        }
    )

    try:
        vectors = _call_embedding_api(normalized_config, normalized_config.model, ["测试连接"])
        vector_dim = len(vectors[0]) if vectors and vectors[0] else 0
        logger.info("EMBEDDING_TEST_SUCCESS=true")
        return {
            "success": True,
            "message": f"Embedding 连接成功，向量维度 {vector_dim}",
            "response": normalized_config.model,
            "vector_dim": vector_dim,
            "error_type": "",
        }
    except httpx.HTTPStatusError as exc:
        error_type, error_summary = _classify_embedding_error(exc)
        logger.info("EMBEDDING_TEST_SUCCESS=false")
        logger.info("EMBEDDING_ERROR_TYPE=%s", error_type)
        detail = ""
        try:
            detail = exc.response.text[:200]
        except Exception:
            detail = ""
        return {
            "success": False,
            "message": f"{error_summary}: {detail}".strip(": "),
            "response": "",
            "vector_dim": 0,
            "error_type": error_type,
        }
    except httpx.RequestError as exc:
        error_type, error_summary = _classify_embedding_error(exc)
        logger.info("EMBEDDING_TEST_SUCCESS=false")
        logger.info("EMBEDDING_ERROR_TYPE=%s", error_type)
        return {
            "success": False,
            "message": f"{error_summary}: {exc}",
            "response": "",
            "vector_dim": 0,
            "error_type": error_type,
        }
    except Exception as exc:
        error_type, error_summary = _classify_embedding_error(exc)
        logger.info("EMBEDDING_TEST_SUCCESS=false")
        logger.info("EMBEDDING_ERROR_TYPE=%s", error_type)
        return {
            "success": False,
            "message": f"{error_summary}: {exc}",
            "response": "",
            "vector_dim": 0,
            "error_type": error_type,
        }


test_embedding_connection.__test__ = False


def generate_embeddings(doc_id: str) -> dict:
    doc = get_document(doc_id)
    if doc is None:
        raise ValueError(f"文档不存在: {doc_id}")

    file_path = Path(doc.file_path)
    if not file_path.exists():
        raise FileNotFoundError(f"原始文件不存在: {doc.file_path}。请检查文件是否被移动或删除。")

    ext = file_path.suffix.lower()
    if ext == ".docx":
        from app.services.parser.docx_parser import parse_docx_file

        result = parse_docx_file(str(file_path))
        full_text = result.get("content", "")
    else:
        from app.services.parser.txt_parser import parse_text_file

        result = parse_text_file(str(file_path))
        full_text = result.get("content", "")

    semantic_chunks = semantic_chunk_document(
        full_text,
        document_id=doc_id,
        source_file=file_path.name,
        source_path=str(file_path.resolve()),
        title=doc.title,
        paragraphs=result.get("paragraphs"),
    )
    chunks = [chunk["chunk_text"] for chunk in semantic_chunks]
    if not chunks:
        logger.error("SEMANTIC_CHUNK_FAILED doc_id=%s reason=empty_content", doc_id)
        raise ValueError("文档内容为空，无法生成向量。")

    missing_fields = sorted(
        {
            key
            for chunk in semantic_chunks
            for key in ("document_type", "level", "org_type", "business_domain", "writing_purpose")
            if chunk.get(key) in (None, "", "未知", "unknown")
        }
    )
    logger.info(
        "SEMANTIC_CHUNK_READY doc_id=%s source_file=%s title=%s paragraphs=%s chunks=%s short_merges=%s "
        "secondary_splits=%s missing_metadata=%s",
        doc_id,
        file_path.name,
        doc.title,
        len(result.get("paragraphs") or []),
        len(semantic_chunks),
        sum(bool(chunk.get("short_merged")) for chunk in semantic_chunks),
        sum(bool(chunk.get("secondary_split")) for chunk in semantic_chunks),
        ",".join(missing_fields) or "none",
    )

    emb_cfg, model = _get_embedding_config()
    try:
        vectors = _call_embedding_api(emb_cfg, model, chunks)
    except httpx.HTTPStatusError as exc:
        error_type, error_summary = _classify_embedding_error(exc)
        raise ValueError(f"{error_summary}: {exc.response.text[:200]}") from exc
    except httpx.TimeoutException as exc:
        error_type, error_summary = _classify_embedding_error(exc)
        raise ValueError(error_summary) from exc
    except httpx.RequestError as exc:
        error_type, error_summary = _classify_embedding_error(exc)
        raise ValueError(f"{error_summary}: {exc}") from exc

    embedding_chunks = [
        EmbeddingChunk(
            chunk_index=index,
            text=chunk,
            vector=vector,
            metadata=semantic_chunks[index].get("metadata", {}),
        )
        for index, (chunk, vector) in enumerate(zip(chunks, vectors))
    ]

    embedding_data = {
        "doc_id": doc_id,
        "model": model,
        "embedded_at": datetime.now().isoformat(),
        "chunks": [chunk.model_dump() for chunk in embedding_chunks],
    }
    atomic_write_text(
        _embedding_path(doc_id),
        json.dumps(embedding_data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    doc.has_embedding = True
    doc.embedding_chunks = len(embedding_chunks)
    doc.embedding_model = model
    doc.embedded_at = embedding_data["embedded_at"]
    atomic_write_text(_index_path(doc_id), doc.model_dump_json(indent=2), encoding="utf-8")

    logger.info(
        "SEMANTIC_INDEX_SUCCESS doc_id=%s source_file=%s chunks=%s schema=%s",
        doc_id,
        file_path.name,
        len(embedding_chunks),
        semantic_chunks[0].get("schema_version", "unknown"),
    )

    return {
        "success": True,
        "message": f"向量化完成: {len(embedding_chunks)} 个分块",
        "chunks": len(embedding_chunks),
        "model": model,
    }


def get_embedding_info(doc_id: str) -> dict | None:
    path = _embedding_path(doc_id)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def delete_embeddings(doc_id: str) -> bool:
    path = _embedding_path(doc_id)
    if not path.exists():
        return False
    path.unlink()
    return True
