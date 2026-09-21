"""Opt-in, source-only reading index. Neither a Brief nor fact authority.

Cache literal action/qualifier signals, not interpreted state, permission or IDs.
Original text (including rejected fragments) remains in SourceSubstrate. Internal
callers must use a private local cache; checksums do not defend a hostile writer.
"""
from dataclasses import dataclass
import json
import os
from pathlib import Path
import tempfile
import time

from app.models.full_article import FullArticleContractError, canonical_json, text_sha
from app.services.generation import atomic_evidence as atomic
from app.services.generation.source_substrate import SourceSubstrate, load_source_substrate


VERSION = "material-digest-experiment-1"
SIGNAL_FIELDS = ("negation_markers", "modality_markers", "completion_markers")


def _rules_sha256():
    return text_sha(Path(__file__).read_text(encoding="utf-8"))


def _reading_units(substrate):
    units = []
    spans = sorted({span for _, fragments in substrate.fragments_by_text for span in fragments})
    for span in spans:
        signals = atomic.classify_semantic_state(span)
        units.append({"source_span": span,
                      "actions": [{"offset": offset, "text": action}
                                  for offset, action in atomic._verb_positions(span)],
                      # Deliberately discard semantic_state and state_qualifier:
                      # authoritative state needs current Atomic context.
                      "literal_signals": {key: signals[key] for key in SIGNAL_FIELDS}})
    return units


def _validate_units(units, substrate):
    spans = sorted({span for _, fragments in substrate.fragments_by_text for span in fragments})
    if not isinstance(units, list) or [unit["source_span"] for unit in units] != spans:
        raise ValueError("source span coverage")
    for unit in units:
        if set(unit) != {"source_span", "actions", "literal_signals"}:
            raise ValueError("unit fields")
        span = unit["source_span"]
        actions, signals = unit["actions"], unit["literal_signals"]
        if not isinstance(actions, list) or not isinstance(signals, dict) or set(signals) != set(SIGNAL_FIELDS):
            raise ValueError("signals schema")
        last = -1
        for action in actions:
            if not isinstance(action, dict) or set(action) != {"offset", "text"}:
                raise ValueError("action schema")
            offset, text = action["offset"], action["text"]
            if (type(offset) is not int or offset <= last or not isinstance(text, str)
                    or not text or span[offset:offset + len(text)] != text):
                raise ValueError("action source binding")
            last = offset
        for markers in signals.values():
            if (not isinstance(markers, list) or any(not isinstance(marker, str) or not marker
                    or marker not in atomic._compact(span) for marker in markers)):
                raise ValueError("literal source binding")


@dataclass(frozen=True)
class MaterialDigest:
    substrate: SourceSubstrate
    units_json: str
    rules_sha256: str
    cache_key: str

    def validate_sources(self, retrieval):
        self.substrate.validate_sources(retrieval)
        if self.rules_sha256 != _rules_sha256():
            raise FullArticleContractError("MATERIAL_DIGEST_RULES_CHANGED")
        if not self.substrate.exact_source_spans:
            raise FullArticleContractError("MATERIAL_DIGEST_EXACT_SPANS_REQUIRED")

    def reading_index(self):
        # A fresh mutable view cannot poison the reusable immutable snapshot.
        return {row["source_span"]: row for row in json.loads(self.units_json)}


@dataclass(frozen=True)
class MaterialDigestLoad:
    digest: MaterialDigest
    cache_hit: bool
    elapsed_ms: float


def load_material_digest(retrieval, cache_dir: Path):
    """Explicit experiment entry; never reads a task or calls a project model.

    Timing includes loading/building SourceSubstrate. Its existing source and
    Atomic-rule fingerprint provides invalidation; the Digest owner adds its own.
    """
    started = time.perf_counter()
    root = Path(cache_dir)
    substrate = load_source_substrate(retrieval, root / "substrate").substrate
    binding = {"version": VERSION, "substrate_key": substrate.cache_key,
               "rules_sha256": _rules_sha256()}
    key = text_sha(canonical_json(binding))
    path = root / "digest" / (key + ".json")
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        raw = None
    except OSError as exc:
        raise FullArticleContractError("MATERIAL_DIGEST_CACHE_UNREADABLE") from exc
    if raw is None:
        units = _reading_units(substrate)
        _validate_units(units, substrate)
        payload = {**binding, "reading_units": units}
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
            raise FullArticleContractError("MATERIAL_DIGEST_CACHE_WRITE_FAILED") from exc
        finally:
            if temp_name and Path(temp_name).exists():
                Path(temp_name).unlink()
    else:
        try:
            envelope = json.loads(raw)
            payload = envelope["payload"]
            if (set(envelope) != {"payload", "sha256"}
                    or set(payload) != {*binding, "reading_units"}
                    or any(payload[k] != value for k, value in binding.items())
                    or envelope["sha256"] != text_sha(canonical_json(payload))):
                raise ValueError("binding/checksum")
            units = payload["reading_units"]
            _validate_units(units, substrate)
        except (ValueError, TypeError, KeyError, UnicodeError) as exc:
            raise FullArticleContractError("MATERIAL_DIGEST_CACHE_INVALID") from exc
    digest = MaterialDigest(substrate, canonical_json(units), binding["rules_sha256"], key)
    return MaterialDigestLoad(digest, raw is not None, (time.perf_counter() - started) * 1000)
