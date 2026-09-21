"""Small storage helpers for crash-safe state-file writes."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


def atomic_write_text(path: str | Path, text: str, *, encoding: str = "utf-8") -> None:
    """Write text via same-directory temp file, fsync, then atomic replace."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp_path: str | None = None

    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding=encoding,
            dir=target.parent,
            prefix=f".{target.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temp_path = handle.name
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())

        os.replace(temp_path, target)
        temp_path = None
    finally:
        if temp_path:
            try:
                Path(temp_path).unlink()
            except FileNotFoundError:
                pass
