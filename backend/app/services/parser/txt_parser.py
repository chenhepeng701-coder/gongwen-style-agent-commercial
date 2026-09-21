from pathlib import Path
from typing import Any


def _read_text_with_fallbacks(file_path: Path) -> str:
    encodings = ["utf-8", "utf-8-sig", "gbk", "gb18030"]
    for encoding in encodings:
        try:
            return file_path.read_text(encoding=encoding)
        except UnicodeDecodeError:
            continue
    return file_path.read_text(encoding="utf-8", errors="ignore")


def parse_text_file(file_path: str | Path) -> dict[str, Any]:
    path = Path(file_path)
    text = _read_text_with_fallbacks(path)
    records = [
        {"text": line.strip(), "paragraph_index": index, "kind": "paragraph"}
        for index, line in enumerate(text.splitlines())
        if line.strip()
    ]
    title = records[0]["text"] if records else ""
    if title.startswith("#"):
        title = title.lstrip("#").strip()
    content = "\n".join(item["text"] for item in records)
    return {"title": title, "content": content, "paragraphs": records}
