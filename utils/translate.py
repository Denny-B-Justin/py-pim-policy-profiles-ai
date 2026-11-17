"""
Translation through the mAI gateway (replaces DeepL: `web/lib/deepl.ts`, `rag` query translation).

* `translate_query()`   – short text (a retrieval query) → another language; None on failure.
* `estimate_characters()` – size shown in the “Translate with AI” confirmation dialog.
* `translate_document_to_english()` – text of the original document is extracted, translated in
  ~6 000-character paragraph-aligned chunks (a few in parallel) and returned as a .docx file.
  DeepL kept the source layout; a language model cannot, so the result is a clean text document
  (headings/paragraph breaks preserved).  The stored provenance label is `mai`.
"""

from __future__ import annotations

import io
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

import constants as C
from utils import extract, mai_client

log = logging.getLogger(__name__)

MAX_TRANSLATE_CHARS = C.MAX_TRANSLATE_CHARS
_WORKERS = C.TRANSLATE_WORKERS

_DOC_SYSTEM = (
    "Translate the user's text into English. It is an excerpt of an official public-finance / public investment "
    "management policy document. Return ONLY the translation — no quotes, no commentary, no summary. "
    "Preserve the structure exactly: keep every paragraph break, heading, numbered article / section / clause "
    "label, bullet and table row (as plain text lines, columns separated by ' | '). Do not omit, merge or "
    "paraphrase anything; keep proper names, defined terms and numbers faithful. If part of the text is already "
    "English, leave it unchanged."
)


def translate_query(text: str, language: str) -> str | None:
    """Translate a short text (e.g. a retrieval query); None on any failure so callers fall back."""
    try:
        out = mai_client.generate(
            f"Translate the user's text into {language}. Return ONLY the translation — no quotes, no commentary.",
            text, max_tokens=300, temperature=0.0, retries=2)
        return out.strip() or None
    except Exception:  # noqa: BLE001
        log.warning("Query translation to %s failed", language, exc_info=True)
        return None


def estimate_characters(data: bytes, file_name: str | None) -> int:
    return len(extract.extract_text(data, file_name))


def split_chunks(text: str, limit: int | None = None) -> list[str]:
    """Paragraph-aligned chunks no longer than `limit` characters (over-long paragraphs split on sentences)."""
    limit = limit or C.TRANSLATE_CHUNK_CHARS
    paras = [p for p in re.split(r"\n{2,}", text) if p.strip()]
    pieces: list[str] = []
    for p in paras:
        if len(p) <= limit:
            pieces.append(p)
            continue
        buf = ""
        for s in re.split(r"(?<=[.!?;:])\s+|\n", p):
            if len(buf) + len(s) + 1 > limit and buf:
                pieces.append(buf)
                buf = ""
            while len(s) > limit:                       # pathological: no punctuation at all
                pieces.append(s[:limit]); s = s[limit:]
            buf = (buf + " " + s).strip()
        if buf:
            pieces.append(buf)
    chunks: list[str] = []
    cur = ""
    for p in pieces:
        if cur and len(cur) + len(p) + 2 > limit:
            chunks.append(cur)
            cur = p
        else:
            cur = f"{cur}\n\n{p}" if cur else p
    if cur:
        chunks.append(cur)
    return chunks


def _translate_chunk(chunk: str, source_label: str | None) -> str:
    system = _DOC_SYSTEM + (f" The source language is {source_label}." if source_label else "")
    return mai_client.generate(system, chunk, max_tokens=C.BRIEFING_MAX_TOKENS, temperature=0.1)


def translate_text_to_english(text: str, source_label: str | None = None,
                              progress: Callable[[int, int], None] | None = None) -> str:
    chunks = split_chunks(text)
    if not chunks:
        raise mai_client.MaiError("The document contains no extractable text to translate.")
    done = 0
    results: list[str | None] = [None] * len(chunks)

    def work(i: int) -> None:
        nonlocal done
        results[i] = _translate_chunk(chunks[i], source_label)
        done += 1
        if progress:
            progress(done, len(chunks))

    with ThreadPoolExecutor(max_workers=max(1, _WORKERS)) as pool:
        for fut in [pool.submit(work, i) for i in range(len(chunks))]:
            fut.result()                                # re-raises the first MaiError
    return "\n\n".join(r or "" for r in results)


def to_docx(text: str, title: str | None = None) -> bytes:
    from docx import Document
    from docx.shared import Pt

    doc = Document()
    style = doc.styles["Normal"]
    style.font.name = "Calibri"
    style.font.size = Pt(11)
    if title:
        doc.add_heading(title, level=1)
    for para in re.split(r"\n{2,}", text):
        para = para.strip("\n")
        if not para.strip():
            continue
        p = doc.add_paragraph()
        lines = para.split("\n")
        for i, line in enumerate(lines):
            run = p.add_run(line)
            if i < len(lines) - 1:
                run.add_break()
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def translate_document_to_english(data: bytes, file_name: str | None, source_label: str | None = None,
                                  progress: Callable[[int, int], None] | None = None) -> tuple[bytes, str]:
    """→ (docx bytes, suggested file name `<stem>_ENG.docx`)."""
    text = extract.extract_text(data, file_name)
    if not text.strip():
        raise mai_client.MaiError("The document has no text layer to translate (scanned image) – upload an English "
                                  "version instead.")
    if len(text) > MAX_TRANSLATE_CHARS:
        raise mai_client.MaiError(f"The document is too long to translate automatically "
                                  f"({len(text):,} characters; the limit is {MAX_TRANSLATE_CHARS:,}).")
    english = translate_text_to_english(text, source_label, progress)
    stem = re.sub(r"\.[^.]+$", "", file_name or "document")
    return to_docx(english), f"{stem}_ENG.docx"
