"""
Text extraction + text-layer detection (ports of `web/lib/extract-text.ts`, `web/lib/text-layer.ts`
and the LangChain loaders PyPDFLoader / Docx2txtLoader / TextLoader).
"""

from __future__ import annotations

import io
import re

import constants as C

_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")   # same scrub as rag/ingestion/sanitize.py
SAMPLE_PAGES = C.EXTRACT_SAMPLE_PAGES
MIN_CHARS_PER_PAGE = C.EXTRACT_MIN_CHARS_PER_PAGE
MAX_DETECT_BYTES = C.EXTRACT_MAX_DETECT_BYTES


def clean(text: str) -> str:
    return _CONTROL.sub("", text or "")


def ext_of(file_name: str | None) -> str:
    m = re.search(r"\.([A-Za-z0-9]{1,8})$", file_name or "")
    return m.group(1).lower() if m else ""


def load_pages(data: bytes, file_name: str | None) -> list[tuple[int | None, str]]:
    """Return [(page_number_or_None, text)] – one entry per PDF page, one for DOCX / TXT / XLSX."""
    ext = ext_of(file_name)
    if ext == "pdf" or data[:5] == b"%PDF-":
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(data))
        pages = []
        for i, page in enumerate(reader.pages, start=1):
            try:
                txt = page.extract_text() or ""
            except Exception:  # noqa: BLE001 - a single bad page must not kill the document
                txt = ""
            pages.append((i, clean(txt)))
        return pages
    if ext == "docx":
        return [(None, clean(_docx_text(data)))]
    if ext == "txt":
        return [(None, clean(data.decode("utf-8", errors="replace")))]
    if ext in ("xlsx", "xlsm"):
        return [(None, clean(_xlsx_text(data)))]
    raise ValueError(f"Unsupported file type: .{ext or '?'}")


def _docx_text(data: bytes) -> str:
    from docx import Document

    doc = Document(io.BytesIO(data))
    parts = [p.text for p in doc.paragraphs if p.text.strip()]
    for table in doc.tables:
        for row in table.rows:
            cells = [c.text.strip() for c in row.cells if c.text.strip()]
            if cells:
                parts.append(" | ".join(cells))
    return "\n".join(parts)


def _xlsx_text(data: bytes, max_cells: int = 200_000) -> str:
    """Workbook → text: one "Sheet: <name>" block per sheet, one line per non-empty row ("a | b | c").
    Cached values are used for formulas; very large workbooks are cut at `max_cells` cells."""
    from openpyxl import load_workbook

    wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    parts, seen = [], 0
    try:
        for ws in wb.worksheets:
            rows = []
            for row in ws.iter_rows(values_only=True):
                seen += len(row)
                cells = [str(v).strip() for v in row if v is not None and str(v).strip()]
                if cells:
                    rows.append(" | ".join(cells))
                if seen >= max_cells:
                    break
            if rows:
                parts.append(f"Sheet: {ws.title}\n" + "\n".join(rows))
            if seen >= max_cells:
                break
    finally:
        wb.close()
    return "\n\n".join(parts)


def extract_text(data: bytes, file_name: str | None, max_bytes: int = 20 * 1024 * 1024) -> str:
    """Plain text of a document (used for the translation length estimate / translation itself)."""
    if len(data) > max_bytes:
        raise ValueError(f"Document is too large to process ({len(data) / 1048576:.1f} MB).")
    ext = ext_of(file_name)
    if ext not in ("pdf", "docx", "txt"):
        raise ValueError(f"Cannot extract text from .{ext or '?'} files")
    return "\n\n".join(t for _, t in load_pages(data, file_name) if t.strip())


def detect_text_layer(data: bytes, file_name: str | None) -> bool | None:
    """True = text layer present (or docx/txt); False = image-only PDF; None = undetermined."""
    ext = ext_of(file_name)
    if ext in ("txt", "docx"):
        return True
    if ext != "pdf" and data[:5] != b"%PDF-":
        return None
    if len(data) > MAX_DETECT_BYTES:
        return None
    try:
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(data))
        n = len(reader.pages)
        if n == 0:
            return None
        k = min(SAMPLE_PAGES, n)
        idx = {int((i * (n - 1)) / max(1, k - 1)) for i in range(k)}   # spread the sample over the document
        chars = 0
        for i in idx:
            try:
                chars += len((reader.pages[i].extract_text() or "").strip())
            except Exception:  # noqa: BLE001
                pass
        return chars / len(idx) >= MIN_CHARS_PER_PAGE
    except Exception:  # noqa: BLE001
        return None
