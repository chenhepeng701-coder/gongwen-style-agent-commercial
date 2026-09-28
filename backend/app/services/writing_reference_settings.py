"""User selection of a private local resource; the schema remains in its owner."""
import os
import tempfile
from pathlib import Path

from app.config import load_settings, save_settings
from app.models.full_article import FullArticleContractError
from app.services.generation.writing_reference_pack import load_writing_reference_pack, validate_writing_reference_bytes
from app.utils.runtime_paths import get_resources_dir

MAX_REFERENCE_BYTES = 5 * 1024 * 1024


def reference_status() -> dict:
    try:
        load_writing_reference_pack()
    except (FullArticleContractError, ValueError):
        return {"configured": False}
    return {"configured": True}


def configure_reference(raw: bytes) -> dict:
    if len(raw) > MAX_REFERENCE_BYTES:
        raise FullArticleContractError("WRITING_REFERENCE_PACK_TOO_LARGE")
    pack = validate_writing_reference_bytes(raw)
    filename = f"writing-reference-{pack.sha256}.json"
    directory = get_resources_dir()
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=directory, suffix=".tmp", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, directory / filename)
        temporary = None
        settings = load_settings()
        settings.writing_reference_resource = filename
        save_settings(settings)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return {"configured": True}
