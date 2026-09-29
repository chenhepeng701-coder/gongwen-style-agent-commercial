"""Production admission only: exact BASE parity, real Registry and offline API.

This is gate acceptance, not generated-article or commercial-quality acceptance.
Run under the existing guarded offline runner. No real Writer or library access.
"""
from dataclasses import fields, is_dataclass
from enum import Enum
from pathlib import Path
import subprocess
import sys
from types import CodeType, FunctionType, ModuleType

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from app.routers import generation
from app.services.generation.commercial_genre_runtime import CommercialGenreRuntime
from app.services.generation.genres import registry
from app.services.generation.genres.catalog import genre_catalog
from app.services.generation.genres.commercial_contract import CommercialMaturity


BASE_SHA = "b74f3603b14106fa1637146ad035443eb84bcfe1"
ROOT = Path(__file__).resolve().parents[2]
GENRES = "backend/app/services/generation/genres/"
GATE_FIELDS = {("capabilities", "status"), ("capabilities", "production_ready"), ("commercial", "maturity")}
PRODUCTION_PATHS = (
    GENRES + "upstream_suite.py", GENRES + "upstream_genre_contracts.py",
    "frontend/src/components/WritingCatalogSelects.jsx", "frontend/src/utils/genreCatalog.js",
)
PROTECTED_PATHS = (
    GENRES + "work_summary.py", GENRES + "commercial_gate.py", GENRES + "registry.py",
    "backend/app/services/retrieval/document_retriever.py",
    *("backend/app/services/generation/" + name for name in (
        "task_assembly.py", "official_document_drafting.py", "full_article_quality.py",
        "atomic_evidence.py", "flow3_v3_trace.py", "full_draft_generation.py", "delivery_decision.py")),
)


def base_bytes(path):
    return subprocess.check_output(["git", "show", f"{BASE_SHA}:{path}"], cwd=ROOT)


def fingerprint(value):
    """Include every descriptor field and callable identity/code/captured state."""
    if isinstance(value, Enum):
        return (type(value).__module__, type(value).__qualname__, value.value)
    if is_dataclass(value):
        return {field.name: fingerprint(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, FunctionType):
        return (value.__module__, value.__qualname__, fingerprint(value.__code__),
                fingerprint(value.__defaults__), fingerprint(value.__kwdefaults__),
                tuple(fingerprint(cell.cell_contents) for cell in (value.__closure__ or ())))
    if isinstance(value, CodeType):
        return (value.co_code.hex(), fingerprint(value.co_consts), value.co_names,
                value.co_varnames, value.co_freevars, value.co_cellvars,
                value.co_argcount, value.co_kwonlyargcount, value.co_posonlyargcount)
    if isinstance(value, (tuple, list)):
        return tuple(fingerprint(item) for item in value)
    if isinstance(value, (set, frozenset)):
        return sorted(fingerprint(item) for item in value)
    if isinstance(value, dict):
        return {key: fingerprint(item) for key, item in value.items()}
    assert value is None or isinstance(value, (str, int, float, bool, bytes)), type(value)
    return value


def without_gate_fields(adapter):
    result = fingerprint(adapter)
    for owner, field in GATE_FIELDS:
        del result[owner][field]
    return result


@pytest.fixture
def base_adapters(monkeypatch):
    package = "app.services.generation.genres"
    with monkeypatch.context() as patch:
        loaded = {}
        for name in ("upstream_genre_contracts", "upstream_suite", "work_summary"):
            path = GENRES + name + ".py"
            module = ModuleType(package + "." + name)
            module.__file__ = str(ROOT / path)
            module.__package__ = package
            patch.setitem(sys.modules, module.__name__, module)
            exec(compile(base_bytes(path), module.__file__, "exec"), module.__dict__)
            loaded[name] = module
        baseline = (*loaded["upstream_suite"].build_suite_adapters(),
                    loaded["work_summary"].WORK_SUMMARY_ADAPTER)
    return {adapter.genre_id: adapter for adapter in baseline}


def test_all_22_real_registry_genres_have_production_admission():
    adapters = registry.GENRE_REGISTRY.all()
    assert len(adapters) == 22
    for adapter in adapters:
        assert adapter.capabilities.status == "VERIFIED_CORE_GENRE"
        assert adapter.capabilities.production_ready is True
        assert adapter.commercial.maturity is CommercialMaturity.PRODUCTION_ENABLED
        assert registry.resolve_genre(adapter.document_type, purpose="production") is adapter
        assert CommercialGenreRuntime.admit(adapter.genre_id).adapter is adapter
    assert not any(a.commercial.maturity is CommercialMaturity.COMMERCIALLY_VERIFIED for a in adapters)


def test_upstream_21_non_gate_fields_and_entire_work_summary_equal_required_base(base_adapters):
    upstream_count = 0
    for adapter in registry.GENRE_REGISTRY.all():
        baseline = base_adapters[adapter.genre_id]
        if adapter.genre_id == "work_summary":
            assert fingerprint(adapter) == fingerprint(baseline)
        else:
            assert baseline.capabilities.status == "EXPERIMENTAL"
            assert baseline.capabilities.production_ready is False
            assert baseline.commercial.maturity is CommercialMaturity.ENGINEERING_READY
            assert without_gate_fields(adapter) == without_gate_fields(baseline), adapter.genre_id
            upstream_count += 1
    assert upstream_count == 21


def test_real_catalog_and_http_catalog_are_the_same_22_production_genres(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Catalog acceptance must never invoke generation")

    monkeypatch.setattr(generation._generator, "generate_commercial_flow", forbidden)
    catalog = genre_catalog()
    app = FastAPI()
    app.include_router(generation.router, prefix="/api/generation")
    with TestClient(app) as client:
        response = client.get("/api/generation/genres")
    assert response.status_code == 200
    assert response.json() == catalog
    rows = catalog["genres"]
    assert len(rows) == 22
    assert [row["genre_id"] for row in rows] == [a.genre_id for a in registry.GENRE_REGISTRY.all()]
    for row in rows:
        assert row["production_ready"] is True
        assert row["generation_route"] == "full_article_drafting"
        assert row["commercial_maturity"] == "PRODUCTION_ENABLED"


@pytest.mark.parametrize("path", PROTECTED_PATHS)
def test_protected_production_owners_are_exact_required_base_bytes(path):
    # The checkout is UTF-8/LF for these owners; any byte change is forbidden.
    assert (ROOT / path).read_bytes() == base_bytes(path), path


def test_only_authorized_gate_fields_change_in_backend_source():
    substitutions = {
        GENRES + "upstream_suite.py": (
            b'GenreCapabilities(status="EXPERIMENTAL", production_ready=False,',
            b'GenreCapabilities(status="VERIFIED_CORE_GENRE", production_ready=True,'),
        GENRES + "upstream_genre_contracts.py": (
            b'maturity=CommercialMaturity.ENGINEERING_READY,',
            b'maturity=CommercialMaturity.PRODUCTION_ENABLED,'),
    }
    for path, (before, after) in substitutions.items():
        baseline = base_bytes(path)
        assert baseline.count(before) == 1
        assert (ROOT / path).read_bytes() == baseline.replace(before, after, 1)


def test_frontend_has_no_internal_user_status_text():
    forbidden = ("已可生产", "已接入，待商业验收")
    for path in (ROOT / "frontend/src").rglob("*"):
        if path.is_file():
            content = path.read_text(encoding="utf-8")
            assert not any(value in content for value in forbidden), path
