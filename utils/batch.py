"""
Spreadsheet batch import – ports of `web/lib/batch/{parse,queries,process,fetch-document}.ts` and the three
`app/api/records/batch/**` routes.

Flow (same as the original)
  1. `parse_and_store`  validates the sheet, decides create / update / skip per row (duplicate = same country +
                        case-insensitive English name) and persists a batch + rows.  Nothing touches live records yet.
  2. `run_batch`        job body: claims one row at a time, creates/updates the record, fetches the linked documents
                        (SSRF-guarded), optionally machine-translates a missing English version with the mAI gateway
                        (the original used DeepL) and stores the result per row.
Rows live in `policy_repo_batch_import_rows` (raw / errors are JSON text – they were Postgres jsonb).
"""

from __future__ import annotations

import csv
import io
import json
import logging
import re
from datetime import datetime, timedelta
from urllib.parse import unquote, urljoin, urlparse

import requests

import constants as C
from utils import db, documents, indexer, mai_client, queries, translate
from utils.audit import log_audit
from utils.check_link import assert_public_host
from utils.db import table

log = logging.getLogger(__name__)

COLUMNS = (
    "country", "name_eng", "name_orig", "original_language", "original_doc_url",
    "english_doc_url", "english_source", "auto_translate", "year", "year_revised", "pages", "source",
    "policy_guidance_tier", "strategy_classification", "jurisdiction_level", "jurisdiction_name",
    "record_status", "superseded_by_link", "superseded_by_note", "machine_readable",
    "overview", "external_link", "comment",
)
REQUIRED = ("country", "name_eng")
ACCEPTED_EXT = tuple(e.lstrip(".") for e in C.ALLOWED_DOC_EXTENSIONS)
MAX_REDIRECTS = C.BATCH_MAX_REDIRECTS
FETCH_TIMEOUT = C.BATCH_FETCH_TIMEOUT
MAX_DOC_BYTES = C.BATCH_MAX_DOC_BYTES
STUCK_ROW = timedelta(minutes=C.BATCH_STUCK_ROW_MINUTES)   # a row "processing" for longer than this is assumed abandoned
LANG_LABELS = dict(C.NATIVE_LANGUAGES)


class BatchError(ValueError):
    """User-facing error (shown in the red alert of the page)."""


# ══════════════════════════════════════════════════════════════════════════════
# parse.ts
# ══════════════════════════════════════════════════════════════════════════════
def _norm_header(h: str) -> str:
    h = (h or "").lower().replace("*", "").strip()
    h = re.sub(r"[\s-]+", "_", h)
    return re.sub(r"[^a-z0-9_]", "", h)


def _cell_text(v) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return str(v)
    if isinstance(v, float) and v.is_integer():     # 2021.0 → "2021" (JS String(2021))
        return str(int(v))
    if isinstance(v, datetime):
        return v.date().isoformat()
    return str(v).strip()


def _row_values(cells: list, colmap: dict[int, str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for idx, key in colmap.items():
        s = _cell_text(cells[idx]) if idx < len(cells) else ""
        if key == "country":
            s = s.upper()
        if key in ("original_language", "jurisdiction_level", "record_status", "machine_readable"):
            s = s.lower()
        if s:
            out[key] = s
    return out


def validate(v: dict[str, str]) -> list[str]:
    e: list[str] = []
    if not v.get("country"):
        e.append("country is required")
    elif not re.fullmatch(r"[A-Z]{3}", v["country"]):
        e.append("country must be a 3-letter ISO3 code")
    if not v.get("name_eng"):
        e.append("name_eng is required")
    # Language is required only when an original document is provided – so it can be classified.
    if v.get("original_doc_url") and not v.get("original_language"):
        e.append("original_language is required when an original document link is given (use 'en' if it is in English)")

    def intval(k):
        s = v.get(k)
        return int(s) if s and re.fullmatch(r"-?\d+(\.0+)?", s) else None

    if v.get("policy_guidance_tier"):
        n = intval("policy_guidance_tier")
        if n is None or n < 1 or n > 4:
            e.append("policy_guidance_tier must be 1–4")
    if v.get("strategy_classification"):
        n = intval("strategy_classification")
        if n is None or n < 1 or n > 6:
            e.append("strategy_classification must be 1–6")
        if v.get("policy_guidance_tier") and intval("policy_guidance_tier") != 4:
            e.append("strategy_classification only applies when policy_guidance_tier = 4")
    if v.get("jurisdiction_level") and v["jurisdiction_level"] not in ("national", "subnational", "supranational"):
        e.append("jurisdiction_level must be one of: national, subnational, supranational")
    if v.get("jurisdiction_level") and v["jurisdiction_level"] != "national" and not v.get("jurisdiction_name"):
        e.append("jurisdiction_name is required when jurisdiction_level is subnational or supranational")
    if v.get("record_status") and v["record_status"] not in ("latest", "archived"):
        e.append("record_status must be one of: latest, archived")
    if v.get("machine_readable") and v["machine_readable"] not in ("y", "n", "yes", "no"):
        e.append("machine_readable must be Y or N")
    for k in ("original_doc_url", "english_doc_url", "external_link"):
        if v.get(k) and not re.match(r"https?://", v[k], re.I):
            e.append(f"{k} must start with http(s)://")
    if v.get("english_source") and v["english_source"].lower() not in ("official", "deepl", "google", "wbg", "other"):
        e.append("english_source must be one of: official, deepl, google, wbg, other")
    if v.get("year") and not re.fullmatch(r"\d{4}", v["year"]):
        e.append("year must be a 4-digit year")
    if v.get("year_revised") and not re.fullmatch(r"\d{4}", v["year_revised"]):
        e.append("year_revised must be a 4-digit year")
    if v.get("pages") and not re.fullmatch(r"\d+", v["pages"]):
        e.append("pages must be a whole number")
    return e


def _grid_xlsx(data: bytes) -> list[tuple[int, list]]:
    from openpyxl import load_workbook

    wb = load_workbook(io.BytesIO(data), data_only=True)
    ws = wb["Policies"] if "Policies" in wb.sheetnames else wb.worksheets[0]
    grid = []
    for row in ws.iter_rows():
        cells = []
        for c in row:
            v = c.value
            if (v is None or v == "") and getattr(c, "hyperlink", None) is not None:
                v = c.hyperlink.target
            cells.append(v)
        if any(_cell_text(v) for v in cells):                 # ExcelJS eachRow({includeEmpty:false})
            grid.append((row[0].row, cells))
    return grid


def _grid_csv(data: bytes) -> list[tuple[int, list]]:
    text = data.decode("utf-8-sig", errors="replace")
    return [(i + 1, row) for i, row in enumerate(csv.reader(io.StringIO(text, newline="")))]


def parse_batch_file(data: bytes, file_name: str) -> dict:
    """→ {"rows": [{row_number, values, errors, is_example}], "header_errors": [str]}"""
    grid = _grid_csv(data) if re.search(r"\.csv$", file_name or "", re.I) else _grid_xlsx(data)
    if not grid:
        return {"rows": [], "header_errors": ["The file appears to be empty."]}
    headers = [_cell_text(c) for c in grid[0][1]]
    colmap: dict[int, str] = {}
    for idx, h in enumerate(headers):
        n = _norm_header(h)
        if n in COLUMNS:
            colmap[idx] = n
    missing = [k for k in REQUIRED if k not in colmap.values()]
    if missing:
        return {"rows": [], "header_errors": [f"Missing required column(s): {', '.join(missing)}"]}
    rows = []
    for row_number, cells in grid[1:]:
        values = _row_values(cells, colmap)
        if not values:
            continue                                            # skip blank rows
        is_example = "EXAMPLE ROW" in values.get("comment", "").upper()
        rows.append({"row_number": row_number, "values": values, "errors": [] if is_example else validate(values),
                     "is_example": is_example})
    return {"rows": rows, "header_errors": []}


# ══════════════════════════════════════════════════════════════════════════════
# queries.ts
# ══════════════════════════════════════════════════════════════════════════════
def find_existing_record_id(country: str, name_eng: str) -> str | None:
    rows = db.query(f"SELECT id FROM {table('policy_records')} WHERE country = :c AND LOWER(name_eng) = LOWER(:n)",
                    {"c": country, "n": name_eng})
    return rows[0]["id"] if rows else None


def create_batch(meta: dict, rows: list[dict]) -> dict:
    ts = db.now()
    batch = {"id": db.uid(), "file_name": meta.get("file_name"), "status": "ready", "total_rows": len(rows),
             "valid_rows": sum(1 for r in rows if r["status"] == "pending"), "created_rows": 0, "failed_rows": 0,
             "auto_translate": 1 if meta["auto_translate"] else 0, "conflict_policy": meta["conflict_policy"],
             "created_by_email": meta.get("created_by_email"), "created_at": ts, "updated_at": ts}
    db.insert("batch_imports", batch)
    for r in rows:
        db.insert("batch_import_rows", {
            "id": db.uid(), "batch_id": batch["id"], "row_number": r["row_number"],
            "raw": json.dumps(r["raw"], ensure_ascii=False), "status": r["status"], "action": r.get("action"),
            "record_id": r.get("record_id"), "errors": json.dumps(r["errors"]) if r.get("errors") else None,
            "note": r.get("note"), "created_at": ts, "updated_at": ts})
    return batch


def get_batch(batch_id: str | None) -> dict | None:
    if not batch_id:
        return None
    rows = db.query(f"SELECT * FROM {table('batch_imports')} WHERE id = :i", {"i": batch_id})
    return rows[0] if rows else None


def _row(r: dict) -> dict:
    r = dict(r)
    r["raw"] = json.loads(r["raw"]) if r.get("raw") else {}
    r["errors"] = json.loads(r["errors"]) if r.get("errors") else []
    return r


def get_batch_rows(batch_id: str) -> list[dict]:
    return [_row(r) for r in db.query(
        f"SELECT * FROM {table('batch_import_rows')} WHERE batch_id = :b ORDER BY row_number", {"b": batch_id})]


def claim_next_row(batch_id: str) -> dict | None:
    """Next pending row (or one stuck in "processing" past STUCK_ROW), marked "processing".

    UC Delta has no `FOR UPDATE SKIP LOCKED`; a batch is only ever worked by one job at a time
    (`run_batch` is guarded by `_active`), so select-then-update is sufficient."""
    cands = db.query(f"SELECT * FROM {table('batch_import_rows')} WHERE batch_id = :b "
                     f"AND status IN ('pending','processing') ORDER BY row_number LIMIT 50", {"b": batch_id})
    stale_before = db.now() - STUCK_ROW
    for r in cands:
        if r["status"] == "pending" or (db.to_dt(r["updated_at"]) or stale_before) < stale_before:
            db.update("batch_import_rows", r["id"], {"status": "processing", "updated_at": db.now()})
            r["status"] = "processing"
            return _row(r)
    return None


def set_row_result(row_id: str, status: str, record_id: str | None = None, note: str | None = None,
                   errors: list[str] | None = None) -> None:
    db.update("batch_import_rows", row_id, {
        "status": status, "record_id": record_id, "note": note,
        "errors": json.dumps(errors) if errors else None, "updated_at": db.now()})


def refresh_batch(batch_id: str) -> dict:
    r = db.query(
        f"SELECT SUM(CASE WHEN status IN ('created','updated') THEN 1 ELSE 0 END) AS created, "
        f"SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS failed, "
        f"SUM(CASE WHEN status IN ('pending','processing') THEN 1 ELSE 0 END) AS pending, COUNT(*) AS total "
        f"FROM {table('batch_import_rows')} WHERE batch_id = :b", {"b": batch_id})[0]
    created, failed, pending, total = (int(r[k] or 0) for k in ("created", "failed", "pending", "total"))
    status = "processing" if pending > 0 else "completed"
    db.update("batch_imports", batch_id, {"created_rows": created, "failed_rows": failed, "status": status,
                                          "updated_at": db.now()})
    return {"created": created, "failed": failed, "pending": pending, "status": status, "total": total}


def log_batch_completion(batch: dict, counts: dict) -> None:
    log_audit({"email": batch.get("created_by_email")}, "create", "batch", batch["id"],
              f'Batch import "{batch.get("file_name") or "upload"}": {counts["created"]} created, '
              f'{counts["failed"]} failed of {counts["total"]} rows')


# ══════════════════════════════════════════════════════════════════════════════
# POST /api/records/batch/parse
# ══════════════════════════════════════════════════════════════════════════════
def parse_and_store(data: bytes, file_name: str, auto_translate: bool, conflict_policy: str,
                    created_by_email: str | None) -> dict:
    """Validate + persist a batch.  Returns {batch_id, summary, rows(preview)}; raises BatchError."""
    if len(data) > C.MAX_BATCH_SHEET_BYTES:
        raise BatchError("Spreadsheet too large (max 5 MB).")
    if conflict_policy not in ("skip", "update", "create"):
        conflict_policy = "skip"
    try:
        parsed = parse_batch_file(data, file_name)
    except Exception as exc:  # noqa: BLE001
        raise BatchError(f"Could not read the file: {exc or 'unknown error'}") from exc
    if parsed["header_errors"]:
        raise BatchError(" ".join(parsed["header_errors"]))
    data_rows = [r for r in parsed["rows"] if not r["is_example"]]
    if not data_rows:
        raise BatchError("No data rows found (did you fill in the Policies sheet and remove the example rows?).")

    rows = []
    for r in data_rows:
        v = r["values"]
        status, action, record_id = "pending", "create", None
        errors = r["errors"] or None
        if errors:
            status, action = "invalid", None
        else:
            existing = find_existing_record_id(v["country"], v["name_eng"])
            if existing:
                record_id = existing
                if conflict_policy == "skip":
                    status, action = "skipped", "skip"
                elif conflict_policy == "update":
                    status, action = "pending", "update"
                else:
                    status, action = "pending", "create"
        rows.append({"row_number": r["row_number"], "raw": v, "status": status, "action": action,
                     "record_id": record_id, "errors": errors})
    batch = create_batch({"file_name": file_name, "auto_translate": auto_translate,
                          "conflict_policy": conflict_policy, "created_by_email": created_by_email}, rows)
    summary = {"total": len(rows), "valid": sum(1 for r in rows if r["status"] == "pending"),
               "invalid": sum(1 for r in rows if r["status"] == "invalid"),
               "skipped": sum(1 for r in rows if r["status"] == "skipped")}
    summary["will_translate"] = sum(1 for r in rows if will_translate(r["raw"], r["status"], r["action"], auto_translate))
    return {"batch_id": batch["id"], "summary": summary}


def will_translate(v: dict, status: str, action: str | None, auto_translate: bool) -> bool:
    return bool(status != "invalid" and action != "skip" and v.get("original_doc_url") and not v.get("english_doc_url")
                and v.get("original_language") and v["original_language"] != "en" and auto_translate)


def preview_rows(batch_id: str) -> tuple[dict | None, list[dict]]:
    """Rows as the preview table shows them (derived from the stored sheet values)."""
    batch = get_batch(batch_id)
    if not batch:
        return None, []
    auto = bool(batch["auto_translate"])
    out = []
    for r in get_batch_rows(batch_id):
        v = r["raw"]
        out.append({"row_number": r["row_number"], "country": v.get("country", ""), "name_eng": v.get("name_eng", ""),
                    "original_language": v.get("original_language", ""), "has_original": bool(v.get("original_doc_url")),
                    "has_english": bool(v.get("english_doc_url")),
                    "will_translate": will_translate(v, r["status"], r["action"], auto),
                    "status": r["status"], "action": r["action"],
                    "errors": r["errors"], "record_id": r["record_id"], "note": r["note"]})
    return batch, out


# ══════════════════════════════════════════════════════════════════════════════
# fetch-document.ts
# ══════════════════════════════════════════════════════════════════════════════
def resolve_download_url(raw: str) -> str:
    """Best-effort conversion of share / preview links into direct downloads."""
    s = raw.strip()
    try:
        u = urlparse(s)
    except ValueError:
        return s
    h = (u.hostname or "").lower()
    if "drive.google.com" in h:
        qs = dict(p.split("=", 1) for p in u.query.split("&") if "=" in p)
        m = re.search(r"/(?:file/)?d/([^/]+)", u.path)
        gid = m.group(1) if m else qs.get("id")
        if gid:
            return f"https://drive.google.com/uc?export=download&id={gid}"
    if "docs.google.com" in h:
        return s                                    # Google-native docs need an export endpoint – flag, don't guess
    if "sharepoint.com" in h or "1drv.ms" in h or "onedrive.live.com" in h:
        return _with_param(s, "download", "1")
    if "dropbox.com" in h:
        return _with_param(s, "dl", "1")
    return s


def _with_param(url: str, key: str, value: str) -> str:
    u = urlparse(url)
    q = [p for p in u.query.split("&") if p and p.split("=", 1)[0] != key] + [f"{key}={value}"]
    return u._replace(query="&".join(q)).geturl()


def _guess_ext(content_type: str, path: str) -> str:
    m = re.search(r"\.([a-zA-Z0-9]{1,5})(?:$|\?)", path)
    from_url = m.group(1).lower() if m else ""
    if from_url in ACCEPTED_EXT:
        return from_url
    ct = content_type.lower()
    if "pdf" in ct:
        return "pdf"
    if "wordprocessingml" in ct:
        return "docx"
    if "msword" in ct:
        return "doc"
    if "text/plain" in ct:
        return "txt"
    return from_url


def fetch_document(raw_url: str, max_bytes: int = MAX_DOC_BYTES) -> dict:
    """Download one document safely → {data, content_type, file_name, ext}.

    https only, no private / loopback / link-local / metadata hosts (re-checked on every redirect hop),
    size cap, accepted file types only."""
    current = resolve_download_url(raw_url)
    for _ in range(MAX_REDIRECTS + 1):
        u = urlparse(current)
        if u.scheme != "https":
            raise BatchError("Only https:// links are allowed")
        assert_public_host(u.hostname or "")
        try:
            res = requests.get(current, allow_redirects=False, timeout=FETCH_TIMEOUT, stream=True,
                               headers={"user-agent": "PIM-BatchImport/1.0"})
        except requests.RequestException as exc:
            raise BatchError(f"Download failed ({type(exc).__name__})") from exc
        try:
            if 300 <= res.status_code < 400:
                loc = res.headers.get("location")
                if not loc:
                    raise BatchError("Redirect without a location")
                current = urljoin(current, loc)
                continue
            if not res.ok:
                raise BatchError(f"Download failed (HTTP {res.status_code})")
            ct = res.headers.get("content-type", "application/octet-stream")
            declared = int(res.headers.get("content-length") or 0)
            if declared and declared > max_bytes:
                raise BatchError(f"File too large ({declared / 1048576:.1f} MB)")
            buf = bytearray()
            for chunk in res.iter_content(1024 * 256):
                buf.extend(chunk)
                if len(buf) > max_bytes:
                    raise BatchError(f"File too large (>{max_bytes / 1048576:.0f} MB)")
        finally:
            res.close()
        if "text/html" in ct.lower():
            raise BatchError("Link returned a web page, not a file — use a direct-download URL")
        ext = _guess_ext(ct, u.path)
        if ext and ext not in ACCEPTED_EXT:
            raise BatchError(f"Unsupported file type (.{ext})")
        name = unquote(u.path.rsplit("/", 1)[-1]) or "document"
        file_name = name if "." in name else f"document.{ext or 'pdf'}"
        return {"data": bytes(buf), "content_type": ct, "file_name": file_name, "ext": ext or "pdf"}
    raise BatchError("Too many redirects")


# ══════════════════════════════════════════════════════════════════════════════
# process.ts
# ══════════════════════════════════════════════════════════════════════════════
def _num(s: str | None) -> int | None:
    return int(s) if s and re.fullmatch(r"\d+", s) else None


def to_record_input(v: dict) -> dict:
    """Columns with a default (jurisdiction level, record status) are only written when the sheet carries them: an
    "update" row from an older template must not reset a sub-national record to national or un-archive one."""
    out = {
        "country": v["country"], "name_eng": v["name_eng"], "name_orig": v.get("name_orig") or None,
        "year": _num(v.get("year")), "year_revised": _num(v.get("year_revised")), "source": v.get("source") or None,
        "overview": v.get("overview") or None, "policy_guidance_tier": _num(v.get("policy_guidance_tier")),
        "strategy_tier": _num(v.get("strategy_classification")), "comment": v.get("comment") or None,
        "link": v.get("external_link") or None, "pages": _num(v.get("pages")),
    }
    for src, dst in (("jurisdiction_level", "jurisdiction_level"), ("jurisdiction_name", "jurisdiction_name"),
                     ("record_status", "record_status"), ("superseded_by_link", "superseded_by_link"),
                     ("superseded_by_note", "superseded_by_note")):
        if v.get(src):
            out[dst] = v[src]
    return out


def process_row(row: dict, batch: dict) -> dict:
    """Create/update the record, fetch its documents and (if needed + enabled) machine-translate an English version.

    → {"status": created|updated|failed, "record_id", "note", "errors"}"""
    v = row["raw"]
    notes: list[str] = []
    try:
        # Download first, write second: a dead link must not leave a half-imported record behind
        # (the original created the record before fetching, so a failed download orphaned it).
        orig_lang = (v.get("original_language") or "").lower()
        orig = fetch_document(v["original_doc_url"]) if v.get("original_doc_url") else None
        eng = fetch_document(v["english_doc_url"]) if v.get("english_doc_url") else None

        data = to_record_input(v)
        if row["action"] == "update" and row["record_id"]:
            queries.update_record(row["record_id"], data)
            record_id = row["record_id"]
        else:
            record_id = queries.create_record(data)["id"]

        # original-language document
        if orig:
            documents.attach_document(record_id, "ORI", orig["file_name"], orig["data"], lang_code=orig_lang or None,
                                      lang_label=LANG_LABELS.get(orig_lang))

        # English document – provided link, else machine translation (non-English original + enabled)
        if eng:
            src = (v.get("english_source") or "").lower()
            documents.attach_document(record_id, "ENG", eng["file_name"], eng["data"], lang_code="en",
                                      lang_label="English",
                                      translation_source=src if documents.is_translation_source(src) else None)
        elif orig and orig_lang and orig_lang != "en" and batch["auto_translate"]:
            if mai_client.is_configured():
                try:
                    out, name = translate.translate_document_to_english(orig["data"], orig["file_name"],
                                                                        LANG_LABELS.get(orig_lang))
                    documents.attach_document(record_id, "ENG", name, out, lang_code="en", lang_label="English",
                                              translation_source="mai", text_layer=True)
                    notes.append("English produced via mAI")
                except Exception as exc:  # noqa: BLE001
                    notes.append(f"Translation skipped: {_msg(exc)} — attach English manually")
            else:
                notes.append("mAI not configured — attach English manually")

        # an explicit machine_readable column overrides the detected roll-up
        if v.get("machine_readable"):
            queries.update_record(record_id, {"machine_readable": v["machine_readable"] in ("y", "yes")})
        indexer.sync_record_metadata(record_id)        # status / name changes → indexed chunks (original: syncRecordToRag)
        queries.db.invalidate("")
        return {"status": "updated" if row["action"] == "update" else "created", "record_id": record_id,
                "note": " · ".join(notes) or None, "errors": None}
    except Exception as exc:  # noqa: BLE001
        log.warning("batch row %s failed: %s", row.get("row_number"), exc)
        return {"status": "failed", "record_id": None, "note": None, "errors": [_msg(exc)]}


def _msg(e: Exception) -> str:
    return getattr(e, "user_message", None) or str(e) or type(e).__name__


# ══════════════════════════════════════════════════════════════════════════════
# POST /api/records/batch/:id/process  (the client looped over it; here a background job does)
# ══════════════════════════════════════════════════════════════════════════════
import threading  # noqa: E402

_active: set[str] = set()
_active_lock = threading.Lock()


def run_batch(job_id: str, batch_id: str) -> dict:
    """Job body: process every claimable row, then finalise (audit entry exactly once)."""
    with _active_lock:
        if batch_id in _active:
            raise BatchError("This batch is already being imported.")
        _active.add(batch_id)
    try:
        batch = get_batch(batch_id)
        if not batch:
            raise BatchError("Batch not found")
        while True:
            row = claim_next_row(batch_id)
            if not row:
                break
            res = process_row(row, batch)
            set_row_result(row["id"], res["status"], res["record_id"], res["note"], res["errors"])
            refresh_batch(batch_id)
        counts = refresh_batch(batch_id)
        if batch["status"] != "completed":
            log_batch_completion(batch, counts)
        return counts
    finally:
        with _active_lock:
            _active.discard(batch_id)
