from pathlib import Path

WINDOWS_ILLEGAL_FILENAME_CHARS = set('\\/:*?"<>|')
MAX_TITLE_FILENAME_LENGTH = 80


def ensure_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def sanitize_filename(filename: str) -> str:
    safe_chars = []
    for char in filename:
        if char.isalnum() or char in {".", "_", "-"}:
            safe_chars.append(char)
        else:
            safe_chars.append("_")
    return "".join(safe_chars)


def sanitize_title_filename(title: str, fallback: str, max_length: int = MAX_TITLE_FILENAME_LENGTH) -> str:
    source = (title or "").strip() or fallback
    safe_chars = []
    for char in source:
        if char in WINDOWS_ILLEGAL_FILENAME_CHARS or ord(char) < 32:
            safe_chars.append("_")
        else:
            safe_chars.append(char)
    safe_name = "".join(safe_chars).strip(" ._")
    if not safe_name:
        safe_name = fallback
    return safe_name[:max_length].rstrip(" ._") or fallback


def is_allowed_extension(filename: str, allowed_extensions: list[str]) -> bool:
    return Path(filename).suffix.lower() in {ext.lower() for ext in allowed_extensions}
