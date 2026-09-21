"""Opt-in source fragmentation cache; never an Evidence or admission authority.

Reuse the Atomic owner's parser, not its task-bound atoms. No production caller
enables this cache. Callers must explicitly supply a local cache directory.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import tempfile
import time

from app.models.full_article import FullArticleContractError, canonical_json, text_sha


VERSION = "source-substrate-1"
# Raw source attributes only: roles, scores, coverage and task overlays are NOT
# source attributes. These values invalidate the cache but confer no permission.
_SOURCE_FIELDS = (
    "source_title", "document_title", "title", "section_heading", "section_index", "chunk_index",
    "evidence_scope_id", "limitation", "limitations", "writability",
)


def _source_projection(retrieval):
    sources = []
    for chunk in retrieval.get("selected_chunks") or []:
        meta = chunk.get("metadata") or {}
        sources.append({
            "document_id": str(chunk.get("document_id") or chunk.get("doc_id") or ""),
            "chunk_id": str(chunk.get("chunk_id") or meta.get("chunk_id") or ""),
            "text": str(chunk.get("text") or chunk.get("chunk_text") or ""),
            "source_attributes_sha256": text_sha(canonical_json({
                "chunk": {k: chunk[k] for k in _SOURCE_FIELDS if k in chunk},
                "metadata": {k: meta[k] for k in _SOURCE_FIELDS if k in meta},
            })),
        })
    # Retrieval order is reapplied by the existing owner on every task.
    return sorted(sources, key=canonical_json)


def _parser_sha256():
    # Conservative automatic invalidation on owner/rule changes, including
    # helper functions/constants. Normalize checkout line endings only.
    from app.services.generation import atomic_evidence
    return text_sha(Path(atomic_evidence.__file__).read_text(encoding="utf-8"))


@dataclass(frozen=True)
class SourceSubstrate:
    sources_json: str
    fragments_by_text: tuple[tuple[str, tuple[str, ...]], ...]
    exact_source_spans: bool
    parser_sha256: str
    cache_key: str

    def validate_sources(self, retrieval):
        if (canonical_json(_source_projection(retrieval)) != self.sources_json
                or self.parser_sha256 != _parser_sha256()):
            raise FullArticleContractError("SOURCE_SUBSTRATE_INPUT_CHANGED")

    def fragments(self, text, *, exact_source_spans):
        if exact_source_spans != self.exact_source_spans:
            raise FullArticleContractError("SOURCE_SUBSTRATE_MODE_MISMATCH")
        for source_text, fragments in self.fragments_by_text:
            if source_text == text:
                return list(fragments)
        raise FullArticleContractError("SOURCE_SUBSTRATE_SOURCE_MISSING")


@dataclass(frozen=True)
class SourceSubstrateLoad:
    substrate: SourceSubstrate
    cache_hit: bool
    elapsed_ms: float


def load_source_substrate(retrieval, cache_dir: Path, *, exact_source_spans=True):
    """Build/load deterministic local source results, with corruption fail-closed.

    The cache retains original text in full, including parser-rejected material.
    A checksum detects accidental corruption; the private local cache is not a
    security boundary against an actor able to rewrite payload AND checksum.
    """
    from app.services.generation.atomic_evidence import _source_fragments
    started = time.perf_counter()
    sources = _source_projection(retrieval)
    parser_sha = _parser_sha256()
    binding = {"version": VERSION, "parser_sha256": parser_sha,
               "exact_source_spans": exact_source_spans, "sources": sources}
    key = text_sha(canonical_json(binding))
    path = Path(cache_dir) / (key + ".json")
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        raw = None
    except OSError as exc:
        raise FullArticleContractError("SOURCE_SUBSTRATE_CACHE_UNREADABLE") from exc
    if raw is None:
        rows = [{"text": text, "fragments": list(_source_fragments(
            text, exact_source_spans=exact_source_spans))}
            for text in sorted({source["text"] for source in sources})]
        payload = {**binding, "fragments_by_text": rows}
        encoded = canonical_json({"payload": payload,
                                  "sha256": text_sha(canonical_json(payload))}).encode("utf-8")
        temp_name = None
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=path.parent, suffix=".tmp", delete=False) as stream:
                temp_name = stream.name
                stream.write(encoded)
            os.replace(temp_name, path)
        except OSError as exc:
            raise FullArticleContractError("SOURCE_SUBSTRATE_CACHE_WRITE_FAILED") from exc
        finally:
            if temp_name and Path(temp_name).exists():
                Path(temp_name).unlink()
    else:
        try:
            envelope = json.loads(raw)
            payload = envelope["payload"]
            if (set(envelope) != {"payload", "sha256"}
                    or set(payload) != {*binding, "fragments_by_text"}
                    or any(payload[k] != value for k, value in binding.items())
                    or envelope["sha256"] != text_sha(canonical_json(payload))):
                raise ValueError("binding/checksum")
            rows = payload["fragments_by_text"]
            if (not isinstance(rows, list)
                    or [row["text"] for row in rows] != sorted({source["text"] for source in sources})
                    or any(set(row) != {"text", "fragments"}
                           or not isinstance(row["fragments"], list)
                           or any(not isinstance(fragment, str) or not fragment for fragment in row["fragments"])
                           for row in rows)):
                raise ValueError("fragments")
        except (ValueError, TypeError, KeyError, UnicodeError) as exc:
            raise FullArticleContractError("SOURCE_SUBSTRATE_CACHE_INVALID") from exc
    substrate = SourceSubstrate(canonical_json(sources),
        tuple((row["text"], tuple(row["fragments"])) for row in rows),
        exact_source_spans, parser_sha, key)
    return SourceSubstrateLoad(substrate, raw is not None, (time.perf_counter() - started) * 1000)
