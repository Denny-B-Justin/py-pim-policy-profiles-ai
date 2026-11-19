"""
Section-aware chunking for the search index (new uploads, re-index, reconcile).

Pure Python (only `re`, `json`, `hashlib`): no app imports, no Databricks imports.  Bump CHUNKER_VERSION whenever the output can change; the
version is stored on every row of `policy_repo_document_chunks` and in `policy_repo_document_ingest_log`.

Pipeline (one document):

    pages [(page_no | None, text)]                       ← utils/extract.load_pages()
      → normalise         control chars, de-hyphenation, whitespace, repeated running headers / footers
      → one text          pages joined, with a char-offset → page map (sections may span pages)
      → legal sections    Article / Chapter / Part / Title / Section / § / numbered ALL-CAPS headings
                          (EN, ES, FR, PT, IT, DE, RU, ZH, JA markers); text before the first heading is KEPT
      → pieces            recursive split on paragraph / line / sentence / word boundaries, ≤ CHUNK_SIZE chars
      → chunks            pieces packed to ≤ PARENT_SIZE chars; a new chunk starts at a section boundary once the
                          current one is ≥ MIN_CHUNK_CHARS; continuation chunks of the same section repeat the last
                          ≤ OVERLAP_CHARS of the previous chunk
      → rows              + the record's metadata (country, names, year, tiers, status) and `embedding_text`
                          (a short context header + the chunk) – the column AI Search embeds

Changes from v1 (the straight port of the Next.js service):
    * v1 split every *page* separately and silently dropped the text before the first heading of a page whenever
      that heading started within the page's first 200 characters (the `matches[0].start() > 200` preamble rule) –
      typically the end of the previous article, so text was lost on many pages of structured laws.
    * v1 matched headings case-insensitively, so wrapped lines such as "article 12 of the Law …" or numbered list
      items started new "sections"; markers are now case-sensitive and must look like a heading.
    * v1 lost the heading on the continuation of a section on the next page and recorded only the first page.
    * v1 used IGNORECASE on the ALL-CAPS heading rule (so any "3. the ministry" line matched) and a wrong
      Chinese pattern (`条 12` instead of `第十二条`).
    * New: running headers / footers / page numbers are removed, chunks carry page_start + page_end, and a small
      overlap is kept between consecutive chunks of the same section.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter

CHUNKER_VERSION = "pim-chunker-v2.1"            # 2.1: XLSX workbooks are read (utils/extract.py)

CHUNK_SIZE = 1000          # max size of a piece after the recursive split
PARENT_SIZE = 2000         # max size of a stored / embedded chunk (≈ 500 tokens)
MIN_CHUNK_CHARS = 600      # do not close a chunk at a section boundary before it has this many characters
OVERLAP_CHARS = 200        # tail of the previous chunk repeated at the start of a continuation chunk
MAX_HEADING_CHARS = 160

_SEPARATORS = ["\n\n", "\n", ". ", " ", ""]

# ── headings ────────────────────────────────────────────────────────────────
# Case-SENSITIVE markers at the start of a line.  The look-ahead makes sure the marker is a heading
# ("Article 5", "Article 5.", "Article 5 – Scope", "ARTICLE 5 SCOPE") and not a wrapped sentence that happens
# to begin with a reference ("Article 5 of the Law on …" – followed by a lower-case word).
_UPPER = "A-ZÀ-ÖØ-ÞĀ-ŽА-ЯЁ"
_NUM = r"(?:[IVXLCDM]+|\d+(?:[.\-]\d+)*|[A-Z])\b"
_AFTER = rf"(?=[ \t]*$|[ \t]*[.:\-–—)(]|[ \t]+[{_UPPER}0-9«\"“'(])"
_KEYWORDS = [
    "CHAPTER", "Chapter", "CAPÍTULO", "Capítulo", "CAPITULO", "Capitulo", "CAPITOLO", "Capitolo", "CHAPITRE",
    "Chapitre", "KAPITEL", "Kapitel", "ГЛАВА", "Глава",
    "PART", "Part", "PARTE", "Parte", "PARTIE", "Partie", "TEIL", "Teil", "ЧАСТЬ", "Часть",
    "TITLE", "Title", "TÍTULO", "Título", "TITULO", "Titulo", "TITOLO", "Titolo", "TITRE", "Titre",
    "BOOK", "Book", "LIBRO", "Libro", "LIVRE", "Livre",
    "ARTICLE", "Article", "Art.", "ARTÍCULO", "Artículo", "ARTICULO", "Articulo", "ARTIGO", "Artigo", "ARTICOLO",
    "Articolo", "ARTIKEL", "Artikel", "СТАТЬЯ", "Статья",
    "SECTION", "Section", "SECCIÓN", "Sección", "SECCION", "Seccion", "SEZIONE", "Sezione", "SEÇÃO", "Seção",
    "ABSCHNITT", "Abschnitt", "РАЗДЕЛ", "Раздел",
    "SCHEDULE", "Schedule", "ANNEX", "Annex", "ANEXO", "Anexo", "ANNEXE", "Annexe", "APPENDIX", "Appendix",
]
_HEADING_PATTERNS = [
    r"^[ \t]*(?:" + "|".join(re.escape(k) for k in _KEYWORDS) + r")[ \t]*" + _NUM + _AFTER,
    r"^[ \t]*§+[ \t]*\d+[a-z]?\b",
    r"^[ \t]*第[一二三四五六七八九十百千零〇\d]+[编編章节節条條款]",
    # numbered ALL-CAPS headings: "3. GENERAL PROVISIONS", "2.1 PROJECT APPRAISAL"  (case-sensitive on purpose)
    rf"^[ \t]*\d+(?:\.\d+)*\.?[ \t]+[{_UPPER}][{_UPPER}0-9 \t,;'’&/()\-]{{4,}}[ \t]*$",
]
HEADING_RE = re.compile("|".join(f"(?:{p})" for p in _HEADING_PATTERNS), flags=re.MULTILINE)

# ── normalisation ───────────────────────────────────────────────────────────
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_HYPHEN_BREAK = re.compile(r"(?<=[a-zà-ÿа-яё])-\n(?=[a-zà-ÿа-яё])")
_SPACES = re.compile(r"[ \t  -   　]+")
_MANY_NL = re.compile(r"\n{3,}")
_PAGE_NO = re.compile(r"^(?:[-–—\s]*(?:page|página|pagina|page n°|стр\.?|p\.)?\s*\d{1,4}(?:\s*(?:/|of|de|из)\s*\d{1,4})?[-–—\s]*)$",
                      re.IGNORECASE)
_DIGITS = re.compile(r"\d+")


def _clean(text: str) -> str:
    text = _CONTROL.sub("", (text or "").replace("\r\n", "\n").replace("\r", "\n"))
    text = _HYPHEN_BREAK.sub("", text)
    lines = [_SPACES.sub(" ", ln).strip() for ln in text.split("\n")]
    return _MANY_NL.sub("\n\n", "\n".join(lines)).strip()


def _line_key(line: str) -> str:
    return _DIGITS.sub("#", line.strip().lower())


def _strip_running_lines(pages: list[tuple]) -> list[tuple]:
    """Drop running headers / footers (same line, digits ignored, at the top or bottom of ≥ 60 % of the pages,
    min. 3 pages) and bare page numbers at the top / bottom of a page."""
    if len(pages) < 4:
        return pages
    edge = Counter()
    for _, text in pages:
        lines = [ln for ln in text.split("\n") if ln.strip()]
        keys = {_line_key(ln) for ln in lines[:2] + lines[-2:] if not HEADING_RE.match(ln.strip())}
        edge.update(k for k in keys if 0 < len(k) <= 150)
    threshold = max(3, int(0.6 * len(pages) + 0.999))
    running = {k for k, n in edge.items() if n >= threshold}
    out = []
    for page, text in pages:
        lines = text.split("\n")
        idx = [i for i, ln in enumerate(lines) if ln.strip()]
        drop = set()
        for i in idx[:2] + idx[-2:]:
            ln = lines[i].strip()
            if HEADING_RE.match(ln):                     # "Article 7" at the top of many pages is content
                continue
            if _line_key(ln) in running or _PAGE_NO.match(ln):
                drop.add(i)
        out.append((page, _MANY_NL.sub("\n\n", "\n".join(ln for i, ln in enumerate(lines) if i not in drop)).strip()))
    return out


def normalise_pages(pages: list[tuple]) -> list[tuple]:
    """[(page, raw text)] → [(page, clean text)] with empty pages removed."""
    cleaned = [(p, _clean(t)) for p, t in pages]
    cleaned = [(p, t) for p, t in cleaned if t]
    return [(p, t) for p, t in _strip_running_lines(cleaned) if t]


# ── splitting ───────────────────────────────────────────────────────────────
def _recursive_split(text: str, size: int, seps: list[str] | None = None) -> list[str]:
    """RecursiveCharacterTextSplitter without overlap.  Every returned piece is a substring of `text`."""
    seps = _SEPARATORS if seps is None else seps
    if len(text) <= size:
        return [text] if text.strip() else []
    sep, rest = "", []
    for i, s in enumerate(seps):
        if s == "" or s in text:
            sep, rest = s, seps[i + 1:]
            break
    pieces = list(text) if sep == "" else text.split(sep)
    out: list[str] = []
    buf = ""
    for piece in pieces:
        candidate = piece if not buf else buf + sep + piece
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


def split_sections(text: str) -> list[tuple[str | None, int, int]]:
    """→ [(heading | None, start, end)] covering ALL of `text` (nothing before the first heading is dropped)."""
    matches = list(HEADING_RE.finditer(text))
    if not matches:
        return [(None, 0, len(text))]
    sections: list[tuple[str | None, int, int]] = []
    first = matches[0].start()
    if text[:first].strip():
        sections.append(("Preamble" if len(text[:first].strip()) > 200 else None, 0, first))
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        line_end = text.find("\n", m.start())
        heading = text[m.start(): line_end if line_end != -1 else len(text)].strip()
        sections.append((heading[:MAX_HEADING_CHARS] or None, m.start(), end))
    return sections


def _overlap_tail(text: str, n: int) -> str:
    """Last ≤ n characters of `text`, starting at a word boundary."""
    if n <= 0 or len(text) <= n:
        return "" if n <= 0 else text
    tail = text[-n:]
    cut = tail.find(" ")
    return tail[cut + 1:] if 0 <= cut < len(tail) - 1 else tail


def chunk_pages(pages: list[tuple], chunk_size: int = CHUNK_SIZE, parent_size: int = PARENT_SIZE,
                min_chunk: int = MIN_CHUNK_CHARS, overlap: int = OVERLAP_CHARS) -> list[dict]:
    """[(page_number | None, text)] → [{content, page, page_end, section_heading, chunk_index, char_count}].

    Deterministic: the same pages always give the same chunks (chunk ids are `<document id>:<chunk_index>`)."""
    norm = normalise_pages(pages)
    if not norm:
        return []
    parts, starts, page_nos, pos = [], [], [], 0
    for page, text in norm:
        starts.append(pos)
        page_nos.append(page)
        parts.append(text)
        pos += len(text) + 2
    full = "\n\n".join(parts)

    def page_at(offset: int):
        lo, hi = 0, len(starts) - 1
        while lo < hi:                      # last page whose start ≤ offset
            mid = (lo + hi + 1) // 2
            if starts[mid] <= offset:
                lo = mid
            else:
                hi = mid - 1
        return page_nos[lo]

    # pieces: (heading, section index, start, end) – located in `full` so page numbers stay exact
    pieces: list[tuple[str | None, int, int, int]] = []
    for s_idx, (heading, s0, s1) in enumerate(split_sections(full)):
        body = full[s0:s1]
        cursor = 0
        for piece in _recursive_split(body, chunk_size):
            at = body.find(piece, cursor)
            if at < 0:                      # cannot happen (pieces are substrings) – stay safe
                at = cursor
            cursor = at + len(piece)
            pieces.append((heading, s_idx, s0 + at, s0 + at + len(piece)))

    chunks: list[dict] = []
    buf: dict | None = None

    def close():
        if buf:
            chunks.append(buf)

    for heading, s_idx, a, b in pieces:
        text = full[a:b]
        new_section = buf is not None and s_idx != buf["_section"]
        too_big = buf is not None and len(buf["content"]) + 2 + len(text) > parent_size
        if buf is not None and (too_big or (new_section and len(buf["content"]) >= min_chunk)):
            close()
            prev = buf
            buf = None
            if not new_section and overlap > 0:          # continuation of the same section → small overlap
                tail = _overlap_tail(prev["content"], min(overlap, parent_size - len(text) - 2))
                if tail:
                    buf = {"content": tail + "\n\n" + text, "page": page_at(a), "page_end": page_at(b - 1),
                           "section_heading": prev["section_heading"], "_section": s_idx}
                    continue
        if buf is None:
            buf = {"content": text, "page": page_at(a), "page_end": page_at(b - 1), "section_heading": heading,
                   "_section": s_idx}
        else:
            buf["content"] += "\n\n" + text
            buf["page_end"] = page_at(b - 1)
            buf["_section"] = s_idx
            if not buf["section_heading"] and heading:
                buf["section_heading"] = heading
    close()

    out = []
    for i, c in enumerate(chunks):
        out.append({"content": c["content"], "page": c["page"], "page_end": c["page_end"],
                    "section_heading": c["section_heading"], "chunk_index": i, "char_count": len(c["content"])})
    return out


# ── rows of policy_repo_document_chunks ─────────────────────────────────────
# Columns written by the app (created_at / updated_at are set by SQL).  Keep in step with
# utils/schema.py → SCHEMA["document_chunks"].
CHUNK_COLUMNS = [
    "chunk_id", "document_id", "record_id", "lang_type", "lang_code", "chunk_index", "page", "page_end",
    "section_heading", "content", "embedding_text", "char_count", "country", "country_name", "name_eng",
    "name_orig", "year", "source", "policy_guidance_tier", "strategy_tier", "jurisdiction_level",
    "jurisdiction_name", "record_status", "file_name", "file_path", "chunker_version", "row_hash",
]
# Record / document fields copied onto every chunk (filters + context).  Editing a record rewrites them
# (utils/indexer.sync_record_metadata) – AI Search filters only see columns of the index.
RECORD_FIELDS = ["country", "country_name", "name_eng", "name_orig", "year", "source", "policy_guidance_tier",
                 "strategy_tier", "jurisdiction_level", "jurisdiction_name", "record_status"]
_INT_FIELDS = {"chunk_index", "page", "page_end", "char_count", "year", "policy_guidance_tier", "strategy_tier"}


def _int_or_none(v):
    if v is None or v == "":
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def chunk_id(document_id: str, chunk_index: int) -> str:
    return f"{document_id}:{int(chunk_index)}"


def build_embedding_text(row: dict) -> str:
    """The text AI Search embeds: a short context header + the chunk.  The header lets a question that names the
    country, law or article find the passage even when the passage itself never repeats them."""
    title = row.get("name_eng") or ""
    if row.get("lang_type") == "ORI" and row.get("name_orig") and row.get("name_orig") != title:
        title = f"{row['name_orig']} ({title})" if title else row["name_orig"]
    head = [f"Country: {row['country_name'] or row['country']}" if (row.get("country_name") or row.get("country")) else "",
            f"Document: {title}" if title else "",
            f"Year: {row['year']}" if row.get("year") else "",
            f"Section: {row['section_heading']}" if row.get("section_heading") else ""]
    head = [h for h in head if h]
    return ("\n".join(head) + "\n\n" + (row.get("content") or "")) if head else (row.get("content") or "")


def row_hash(row: dict) -> str:
    """Fingerprint of everything that reaches the index; MERGE only rewrites a row when it changes (an unchanged
    row is not re-embedded)."""
    payload = {k: row.get(k) for k in CHUNK_COLUMNS if k not in ("row_hash",)}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")).hexdigest()


def finish_row(row: dict) -> dict:
    """Normalise types, (re)build embedding_text and row_hash."""
    for k in _INT_FIELDS:
        row[k] = _int_or_none(row.get(k))
    row["record_status"] = row.get("record_status") or "latest"
    row["embedding_text"] = build_embedding_text(row)
    row["row_hash"] = row_hash(row)
    return {k: row.get(k) for k in CHUNK_COLUMNS}


def apply_record_metadata(row: dict, record: dict, country_name: str | None) -> dict:
    """Copy the record's descriptive fields onto a chunk row (used for new chunks and after a record edit)."""
    row = dict(row)
    for k in RECORD_FIELDS:
        row[k] = country_name if k == "country_name" else record.get(k)
    if not row.get("country_name"):
        row["country_name"] = record.get("country")
    return finish_row(row)


def chunk_rows(chunks: list[dict], document: dict, record: dict, country_name: str | None,
               file_path: str | None) -> list[dict]:
    """chunk_pages() output + the document / record → rows for policy_repo_document_chunks."""
    rows = []
    for c in chunks:
        base = {
            "chunk_id": chunk_id(document["id"], c["chunk_index"]), "document_id": document["id"],
            "record_id": document["record_id"], "lang_type": document.get("lang_type"),
            "lang_code": document.get("lang_code"), "chunk_index": c["chunk_index"], "page": c.get("page"),
            "page_end": c.get("page_end"), "section_heading": c.get("section_heading"), "content": c["content"],
            "char_count": c.get("char_count", len(c["content"])), "file_name": document.get("file_name"),
            "file_path": file_path, "chunker_version": CHUNKER_VERSION,
        }
        rows.append(apply_record_metadata(base, record, country_name))
    return rows
