"""Opt-in chunk assets: immutable source parsing, never task fact authority.

Precompute from an explicitly complete chunk_text. The caller owns a private
local cache directory; no default library path, upload hook, or model call.
"""
from copy import deepcopy
from dataclasses import dataclass
import json
import os
from pathlib import Path
import tempfile
import time

from app.models.full_article import FullArticleContractError, canonical_json, text_sha
from app.services.generation import material_digest as digest_owner
from app.services.generation import source_substrate as source_owner


VERSION = "stable-material-asset-1"
# Explicit source schema, taken from retrieval/official_chunk_metadata. Unknown
# fields are not silently promoted into stable metadata. document_role and
# content_role can be task overlays, so both are deliberately excluded.
STABLE_SOURCE_FIELDS = tuple(dict.fromkeys((*source_owner._SOURCE_FIELDS,
    "source_file", "source_path", "file_path", "source", "schema_version",
    "document_title_source", "document_type", "level", "org_type",
    "business_domain", "writing_purpose", "section_title", "section_path",
    "section_level", "paragraph_start", "paragraph_end", "paragraph_type",
    "content_reference_type", "is_title_chunk", "is_attachment", "char_count",
    "chunk_length", "style_tags", "quality_flags", "is_official_semantic_chunk",
    "chunk_strategy",
)))


def _rules_sha256():
    return text_sha(Path(__file__).read_text(encoding="utf-8"))


def _binding(chunk):
    meta = chunk.get("metadata") or {}
    if not isinstance(meta, dict):
        raise FullArticleContractError("STABLE_MATERIAL_SOURCE_INVALID")
    document_id = str(chunk.get("document_id") or chunk.get("doc_id") or "")
    chunk_id = str(chunk.get("chunk_id") or meta.get("chunk_id") or "")
    text = chunk.get("chunk_text")
    # Never treat a 300/800-character task text as an implicitly full source.
    if not document_id or not chunk_id or not isinstance(text, str) or not text:
        raise FullArticleContractError("STABLE_MATERIAL_FULL_SOURCE_REQUIRED")
    return {
        "version": VERSION,
        "source_version": source_owner.VERSION,
        "digest_version": digest_owner.VERSION,
        "document_id": document_id, "chunk_id": chunk_id, "chunk_text": text,
        "source_metadata": {
            "chunk": {k: chunk[k] for k in STABLE_SOURCE_FIELDS if k in chunk},
            "metadata": {k: meta[k] for k in STABLE_SOURCE_FIELDS if k in meta},
        },
        "exact_source_spans": True,
        "rules": {
            "atomic_parser": source_owner._parser_sha256(),
            "source_substrate": text_sha(Path(source_owner.__file__).read_text(encoding="utf-8")),
            "material_digest": digest_owner._rules_sha256(),
            "stable_asset": _rules_sha256(),
        },
    }


def _full_substrate(binding, fragments):
    # Internal precomputation only. This object is never returned to Evidence.
    return source_owner.SourceSubstrate("[]", ((binding["chunk_text"], tuple(fragments)),),
        True, binding["rules"]["atomic_parser"], text_sha(canonical_json(binding)))


def _validate_payload(payload, binding):
    if (not isinstance(payload, dict)
            or set(payload) != {*binding, "fragments", "reading_units"}
            or any(payload[k] != value for k, value in binding.items())):
        raise ValueError("asset binding")
    fragments = payload["fragments"]
    if (not isinstance(fragments, list)
            or any(not isinstance(span, str) or not span for span in fragments)):
        raise ValueError("fragment schema")
    digest_owner._validate_units(payload["reading_units"], _full_substrate(binding, fragments))


@dataclass(frozen=True)
class StableMaterialAsset:
    payload_json: str
    cache_key: str

    def source_for(self, chunk):
        """Validate identity, full source, metadata and current rules on reuse."""
        binding = _binding(chunk)
        if text_sha(canonical_json(binding)) != self.cache_key:
            raise FullArticleContractError("STABLE_MATERIAL_ASSET_INPUT_CHANGED")
        try:
            payload = json.loads(self.payload_json)
            _validate_payload(payload, binding)
        except (ValueError, TypeError, KeyError, UnicodeError) as exc:
            raise FullArticleContractError("STABLE_MATERIAL_ASSET_INVALID") from exc
        return payload  # Detached mutable copy; never mutate the asset.


@dataclass(frozen=True)
class StableMaterialAssetLoad:
    asset: StableMaterialAsset
    cache_hit: bool
    elapsed_ms: float


def load_stable_material_asset(chunk, cache_dir: Path):
    """Build/load exactly one chunk; selected sets/order never enter its key."""
    from app.services.generation.atomic_evidence import _source_fragments

    started = time.perf_counter()
    binding = _binding(chunk)
    key = text_sha(canonical_json(binding))
    path = Path(cache_dir) / (key + ".json")
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        raw = None
    except OSError as exc:
        raise FullArticleContractError("STABLE_MATERIAL_CACHE_UNREADABLE") from exc
    if raw is None:
        fragments = list(_source_fragments(binding["chunk_text"], exact_source_spans=True))
        units = digest_owner._reading_units(_full_substrate(binding, fragments))
        payload = {**binding, "fragments": fragments, "reading_units": units}
        _validate_payload(payload, binding)
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
            raise FullArticleContractError("STABLE_MATERIAL_CACHE_WRITE_FAILED") from exc
        finally:
            if temp_name and Path(temp_name).exists():
                Path(temp_name).unlink()
    else:
        try:
            envelope = json.loads(raw)
            payload = envelope["payload"]
            if (set(envelope) != {"payload", "sha256"}
                    or envelope["sha256"] != text_sha(canonical_json(payload))):
                raise ValueError("asset checksum")
            _validate_payload(payload, binding)
        except (ValueError, TypeError, KeyError, UnicodeError) as exc:
            raise FullArticleContractError("STABLE_MATERIAL_CACHE_INVALID") from exc
    return StableMaterialAssetLoad(StableMaterialAsset(canonical_json(payload), key),
        raw is not None, (time.perf_counter() - started) * 1000)


def authorized_retrieval_view(retrieval):
    """Detach full chunk bodies before crossing the Atomic/Task Assembly boundary.

An explicit empty text means no permission, never fallback to chunk_text.
Missing/non-string text fails closed; containment is checked during composition.
"""
    view = deepcopy(retrieval)
    for chunk in view.get("selected_chunks") or []:
        if not isinstance(chunk.get("text"), str):
            raise FullArticleContractError("STABLE_MATERIAL_AUTHORIZED_TEXT_REQUIRED")
        chunk.pop("chunk_text", None)
    return view


def selected_asset_sources(retrieval, assets):
    """Bind every selected chunk to a current asset, without granting permission."""
    by_key = {asset.cache_key: asset for asset in assets}
    sources = []
    for chunk in retrieval.get("selected_chunks") or []:
        key = text_sha(canonical_json(_binding(chunk)))
        asset = by_key.get(key)
        if asset is None:
            raise FullArticleContractError("STABLE_MATERIAL_ASSET_MISSING_OR_STALE")
        payload = asset.source_for(chunk)
        text = chunk.get("text")
        if not isinstance(text, str):
            raise FullArticleContractError("STABLE_MATERIAL_AUTHORIZED_TEXT_REQUIRED")
        if text not in payload["chunk_text"]:
            raise FullArticleContractError("STABLE_MATERIAL_AUTHORIZED_TEXT_OUTSIDE_SOURCE")
        sources.append((text, payload))
    return sources
