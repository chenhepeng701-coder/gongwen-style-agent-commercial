from pathlib import Path
from typing import Any

from docx import Document
from docx.oxml.table import CT_Tbl
from docx.oxml.text.paragraph import CT_P
from docx.table import Table
from docx.text.paragraph import Paragraph


def _iter_body_blocks(document):
    """Yield paragraphs and tables in their original document-body order."""
    for child in document.element.body.iterchildren():
        if isinstance(child, CT_P):
            yield Paragraph(child, document)
        elif isinstance(child, CT_Tbl):
            yield Table(child, document)


def _table_text(table: Table) -> str:
    rows: list[str] = []
    for row in table.rows:
        cells = [" ".join(cell.text.split()) for cell in row.cells]
        if any(cells):
            rows.append(" | ".join(cells))
    if not rows:
        return ""
    return "[表格]\n" + "\n".join(rows)


def parse_docx_file(file_path: str | Path) -> dict[str, Any]:
    document = Document(str(file_path))
    records: list[dict[str, Any]] = []
    for body_index, block in enumerate(_iter_body_blocks(document)):
        if isinstance(block, Paragraph):
            text = block.text.strip()
            kind = "paragraph"
        else:
            text = _table_text(block)
            kind = "table"
        if not text:
            continue
        records.append(
            {
                "text": text,
                "paragraph_index": body_index,
                "kind": kind,
            }
        )

    first_paragraph = next((item["text"] for item in records if item["kind"] == "paragraph"), "")
    content = "\n".join(item["text"] for item in records)
    return {"title": first_paragraph, "content": content, "paragraphs": records}
