"""
Section-aware chunking (port of `rag/app/ingestion/chunkers.py` – the default `parent_doc` strategy).

The original embedded ~400-char *children* and returned ~2000-char section-aware *parents* to the LLM.
Without embeddings (mAI has no embedding endpoint) the parent-sized chunks are what we index and
retrieve with BM25:  legal-section split → recursive split (1000 chars) → pack to ≤ 2000 chars.
"""

from __future__ import annotations

import re

CHUNK_SIZE = 1000
PARENT_SIZE = max(CHUNK_SIZE * 2, 1800)   # 2000

# Legal section markers seen in PIM policy documents across jurisdictions (identical to the original).
_LEGAL_SECTION_PATTERNS = [
    r"^(?:CHAPTER|Chapter|CAPÍTULO|CAPITOLO|ГЛАВА|第[一二三四五六七八九十百]+章)\s+[IVXLCDM\d]+",
    r"^(?:PART|Part|PARTE|ЧАСТЬ)\s+[IVXLCDM\d]+",
    r"^(?:TITLE|Title|TÍTULO|TITOLO)\s+[IVXLCDM\d]+",
    r"^(?:Article|ARTICLE|Artículo|ARTICOLO|Статья|条)\s*[\d\.]+",
    r"^(?:Section|SECTION|Sección|SEZIONE|Раздел)\s*[\d\.]+",
    r"^§\s*\d+",
    r"^\d+\.\s+[A-ZА-Я][A-ZА-Я\s,]{4,}$",
]
_LEGAL_RE = re.compile("|".join(_LEGAL_SECTION_PATTERNS), flags=re.MULTILINE | re.IGNORECASE)
_SEPARATORS = ["\n\n", "\n", ". ", " ", ""]


def _split_legal_sections(text: str) -> list[tuple[str, str]]:
    matches = list(_LEGAL_RE.finditer(text))
    if not matches:
        return [("", text)]
    sections: list[tuple[str, str]] = []
    if matches[0].start() > 200:
        sections.append(("Preamble", text[: matches[0].start()].strip()))
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        sections.append((m.group(0).strip(), text[m.start():end].strip()))
    return sections


def _recursive_split(text: str, size: int, seps: list[str] | None = None) -> list[str]:
    """Minimal RecursiveCharacterTextSplitter (no overlap)."""
    seps = _SEPARATORS if seps is None else seps
    if len(text) <= size:
        return [text] if text.strip() else []
    sep = ""
    rest: list[str] = []
    for i, s in enumerate(seps):
        if s == "" or s in text:
            sep, rest = s, seps[i + 1:]
            break
    pieces = list(text) if sep == "" else text.split(sep)
    out: list[str] = []
    buf = ""
    for piece in pieces:
        candidate = piece if not buf else buf + (sep if sep != ". " else ". ") + piece
        if len(candidate) <= size:
            buf = candidate
            continue
        if buf:
            out.append(buf)
            buf = ""
        if len(piece) > size and rest:
            out.extend(_recursive_split(piece, size, rest))
        else:
            buf = piece
    if buf:
        out.append(buf)
    return [c for c in (x.strip() for x in out) if c]


def chunk_pages(pages: list[tuple[int | None, str]]) -> list[dict]:
    """pages → [{content, page, section_heading, chunk_index}] with ≤ PARENT_SIZE chars each."""
    sections: list[dict] = []
    for page, text in pages:
        text = (text or "").strip()
        if not text:
            continue
        legal = _split_legal_sections(text)
        parts = legal if len(legal) > 1 else [("", text)]
        for heading, body in parts:
            for piece in _recursive_split(body, CHUNK_SIZE):
                sections.append({"content": piece, "page": page, "section_heading": heading or None})

    # Pack consecutive pieces into parent-sized blocks (same loop as `_parent_doc_chunker`).
    parents: list[dict] = []
    buf: dict | None = None
    for s in sections:
        if buf and len(buf["content"]) + len(s["content"]) + 2 > PARENT_SIZE:
            parents.append(buf)
            buf = None
        if buf is None:
            buf = dict(s)
        else:
            buf["content"] += "\n\n" + s["content"]
    if buf:
        parents.append(buf)
    for i, p in enumerate(parents):
        p["chunk_index"] = i
    return parents
