"""Private local Writing Reference resource; never a fact authority or fallback."""
from dataclasses import dataclass, field
from contextlib import contextmanager
from contextvars import ContextVar
import hashlib
import json
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from app.config import get_settings
from app.models.full_article import FullArticleContractError, canonical_json


Nonblank = Annotated[str, StringConstraints(pattern=r"\S")]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)


class _Outline(_Strict):
    line: int = Field(ge=1)
    text: Nonblank


class _Excerpt(_Strict):
    reference_id: Nonblank
    line_start: int = Field(ge=1)
    line_end: int = Field(ge=1)
    text: Nonblank
    teaches: Nonblank
    observation: Nonblank
    transfer_limit: Nonblank

    @model_validator(mode="after")
    def ordered_span(self):
        if self.line_end < self.line_start:
            raise ValueError("Invalid reference line span")
        return self


class _Primary(_Strict):
    role: Literal["PRIMARY_WRITING_EXAMPLE_NOT_FACTS"]
    outline: list[_Outline] = Field(min_length=1)
    excerpts: list[_Excerpt] = Field(min_length=1)
    limitations: list[Nonblank] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_references(self):
        ids = [item.reference_id for item in self.excerpts]
        if len(ids) != len(set(ids)):
            raise ValueError("Duplicate reference identity")
        return self


class _Problem(_Strict):
    role: Literal["PROBLEM_PASSAGE_ONLY"]
    text: Nonblank
    observation: Nonblank
    transfer_limit: Nonblank


class _PackSchema(_Strict):
    # These are existing Writer role slots, not bundled sample content.
    S02: _Primary
    S05: _Problem


@dataclass(frozen=True)
class WritingReferencePack:
    payload_json: str = field(repr=False)
    sha256: str

    def writer_payload(self):
        return json.loads(self.payload_json)


_active_pack: ContextVar[WritingReferencePack | None] = ContextVar("writing_reference_pack", default=None)


@contextmanager
def use_writing_reference_pack(pack: WritingReferencePack):
    """Bind the audited snapshot only while assembling this request's materials."""
    token = _active_pack.set(pack)
    try:
        yield
    finally:
        _active_pack.reset(token)


def current_writing_reference_pack() -> WritingReferencePack:
    pack = _active_pack.get()
    return pack if pack is not None else load_writing_reference_pack()


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def load_writing_reference_pack() -> WritingReferencePack:
    """Read once and bind the exact bytes to the unchanged Writer data shape.

    The digest identifies the supplied content, not redistribution permission.
    No body, local path or validation input is included in public errors/logs.
    """
    configured = get_settings().writing_reference_pack_path.strip()
    if not configured:
        raise FullArticleContractError("WRITING_REFERENCE_PACK_REQUIRED")
    path = Path(configured)
    if not path.is_absolute() or configured.startswith(("\\\\", "//")):
        raise FullArticleContractError("WRITING_REFERENCE_PACK_PATH_INVALID")
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        raise FullArticleContractError("WRITING_REFERENCE_PACK_NOT_FOUND") from None
    except (OSError, ValueError):
        raise FullArticleContractError("WRITING_REFERENCE_PACK_UNREADABLE") from None
    return validate_writing_reference_bytes(raw)


def validate_writing_reference_bytes(raw: bytes) -> WritingReferencePack:
    """Shared validation for the local settings upload and the Writer loader."""
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object)
        _PackSchema.model_validate(value)
        payload_json = canonical_json(value)
    except (ValueError, RecursionError):
        raise FullArticleContractError("WRITING_REFERENCE_PACK_SCHEMA_INVALID") from None
    return WritingReferencePack(payload_json=payload_json, sha256=hashlib.sha256(raw).hexdigest())
