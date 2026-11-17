"""
Query layer – port of `web/modules/{records,documents,map,briefings}/queries.ts`.

Statements use only the SQL subset shared by Databricks SQL and SQLite; the heavier
roll-ups (dashboard, language breakdown, map) are aggregated in Python from two
narrow SELECTs, which keeps them dialect-independent and fast enough for the
repository's size (hundreds to a few thousand records).  Results of the read-mostly
roll-ups are cached for `PIM_CACHE_SECONDS` (default 60 s – the Next.js `revalidate`).
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Any

import constants as C
from utils import db
from utils.db import table, to_bool, to_dt, to_int

_REC_TS = ("created_at", "updated_at", "link_checked_at", "link_last_ok_at")
_DOC_TS = ("uploaded_at",)


# ── row normalisation (sqlite returns text, Databricks returns native types) ──
def _rec(r: dict) -> dict:
    out = dict(r)
    for k in _REC_TS:
        out[k] = to_dt(out.get(k))
    out["machine_readable"] = to_bool(out.get("machine_readable"))
    for k in ("year", "year_revised", "policy_guidance_tier", "strategy_tier", "pages", "tokens", "link_ok"):
        out[k] = to_int(out.get(k))
    return out


def _doc(r: dict) -> dict:
    out = dict(r)
    out["uploaded_at"] = to_dt(out.get("uploaded_at"))
    out["text_layer"] = to_bool(out.get("text_layer"))
    out["file_size"] = to_int(out.get("file_size"))
    return out


# ── records ──────────────────────────────────────────────────────────────────
def list_records(search: str | None = None, country: str | None = None, policy_tier: int | None = None,
                 strategy_tier: int | None = None, limit: int = 500, offset: int = 0) -> list[dict]:
    where, params = [], {}
    if search:
        params["s"] = f"%{search.lower()}%"
        where.append("(LOWER(name_eng) LIKE :s OR LOWER(country) LIKE :s OR LOWER(COALESCE(overview,'')) LIKE :s)")
    if country:
        where.append("country = :country")
        params["country"] = country
    if policy_tier is not None:
        where.append("policy_guidance_tier = :pt")
        params["pt"] = policy_tier
    if strategy_tier is not None:
        where.append("strategy_tier = :st")
        params["st"] = strategy_tier
    sql = f"SELECT * FROM {table('policy_records')}"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += f" ORDER BY country ASC, LOWER(name_eng) ASC LIMIT {int(limit)} OFFSET {int(offset)}"
    key = f"policy_records:list:{sql}:{sorted(params.items())}"
    return db.cached(key, None, lambda: [_rec(r) for r in db.query(sql, params)])


def get_record(record_id: str) -> dict | None:
    rows = db.query(f"SELECT * FROM {table('policy_records')} WHERE id = :id", {"id": record_id})
    return _rec(rows[0]) if rows else None


def get_documents_for_record(record_id: str) -> list[dict]:
    rows = db.query(f"SELECT * FROM {table('documents')} WHERE record_id = :id", {"id": record_id})
    return [_doc(r) for r in rows]


def get_record_with_docs(record_id: str) -> dict | None:
    rec = get_record(record_id)
    if not rec:
        return None
    rec["documents"] = get_documents_for_record(record_id)
    return rec


_RECORD_COLUMNS = [
    "country", "name_eng", "name_orig", "year", "source", "year_revised", "overview", "policy_guidance_tier",
    "strategy_tier", "jurisdiction_level", "jurisdiction_name", "record_status", "superseded_by_link",
    "superseded_by_note", "machine_readable", "comment", "link", "pages", "tokens",
]


def create_record(data: dict) -> dict:
    ts = db.now()
    row = {c: data.get(c) for c in _RECORD_COLUMNS}
    row["jurisdiction_level"] = row["jurisdiction_level"] or "national"
    row["record_status"] = row["record_status"] or "latest"
    row.update(id=db.uid(), link_ok=None, link_checked_at=None, link_last_ok_at=None, created_at=ts, updated_at=ts)
    db.insert("policy_records", row)
    return _rec(row)


def update_record(record_id: str, data: dict) -> dict | None:
    values = {c: data[c] for c in _RECORD_COLUMNS if c in data}
    if "link" in values:   # link changed → reset reachability, re-checked asynchronously
        values.update(link_ok=None, link_checked_at=None, link_last_ok_at=None)
    values["updated_at"] = db.now()
    db.update("policy_records", record_id, values)
    return get_record(record_id)


def get_record_country(record_id: str) -> str | None:
    rows = db.query(f"SELECT country FROM {table('policy_records')} WHERE id = :id", {"id": record_id})
    return rows[0]["country"] if rows else None


def set_link_status(record_id: str, link_ok: int | None, checked_at: datetime | None,
                    last_ok_at: datetime | None | str = "keep") -> None:
    values: dict[str, Any] = {"link_ok": link_ok, "link_checked_at": checked_at}
    if last_ok_at != "keep":
        values["link_last_ok_at"] = last_ok_at
    db.update("policy_records", record_id, values)


def delete_record(record_id: str) -> list[dict]:
    """Delete a record + its documents + indexed chunks.  Returns the removed document rows
    (so the caller can delete the files from storage)."""
    docs = get_documents_for_record(record_id)
    db.execute(f"DELETE FROM {table('document_chunks')} WHERE record_id = :id", {"id": record_id})
    db.execute(f"DELETE FROM {table('documents')} WHERE record_id = :id", {"id": record_id})
    db.delete("policy_records", record_id)
    db.invalidate("")
    return docs


# ── countries ────────────────────────────────────────────────────────────────
def list_all_countries() -> list[dict]:
    def load():
        return [{"iso3": r["iso3"], "name": r["name"]} for r in
                db.query(f"SELECT iso3, name FROM {table('countries')} ORDER BY name ASC")]
    return db.cached("countries:all", 300, load)


def list_countries_with_context() -> list[dict]:
    """Countries with their GDP / public-investment context (partner API `/api/v1/countries`)."""
    rows = db.query(f"SELECT iso3, name, gdp_2024_bn, pub_inv_pct_gdp, pub_inv_2024_bn FROM {table('countries')} "
                    f"ORDER BY name ASC")
    f = lambda v: None if v is None else float(v)  # noqa: E731  (DECIMAL → number)
    return [{"iso3": r["iso3"], "name": r["name"], "gdp_2024_bn": f(r["gdp_2024_bn"]),
             "pub_inv_pct_gdp": f(r["pub_inv_pct_gdp"]), "pub_inv_2024_bn": f(r["pub_inv_2024_bn"])} for r in rows]


def v1_list_records(where: list[str], params: dict, limit: int, offset: int) -> list[dict]:
    """Filtered record page for the partner API (WHERE fragments are built by the caller from fixed columns only)."""
    sql = f"SELECT * FROM {table('policy_records')}"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += f" ORDER BY country ASC, LOWER(name_eng) ASC LIMIT {int(limit)} OFFSET {int(offset)}"
    return [_rec(r) for r in db.query(sql, params)]


def country_name_map() -> dict[str, str]:
    return {c["iso3"]: c["name"] for c in list_all_countries()}


def get_country(iso3: str) -> dict | None:
    rows = db.query(f"SELECT * FROM {table('countries')} WHERE iso3 = :i", {"i": iso3.upper()})
    return rows[0] if rows else None


def list_countries() -> list[dict]:
    """Countries that have at least one record (distinct) with lookup names."""
    def load():
        rows = db.query(
            f"SELECT DISTINCT p.country AS iso3, COALESCE(c.name, p.country) AS name "
            f"FROM {table('policy_records')} p LEFT JOIN {table('countries')} c ON c.iso3 = p.country "
            f"ORDER BY COALESCE(c.name, p.country)"
        )
        return [{"iso3": r["iso3"], "name": r["name"]} for r in rows]
    return db.cached("policy_records:countries", None, load)


def _all_docs_slim() -> list[dict]:
    return db.cached(
        "documents:slim", None,
        lambda: [_doc(r) for r in db.query(
            f"SELECT id, record_id, lang_type, lang_code, lang_label, translation_source, text_layer, file_name, "
            f"file_size FROM {table('documents')}")],
    )


def _all_records_slim() -> list[dict]:
    return db.cached(
        "policy_records:slim", None,
        lambda: [_rec(r) for r in db.query(
            f"SELECT id, country, name_eng, record_status, link_ok, updated_at, jurisdiction_level "
            f"FROM {table('policy_records')}")],
    )


def list_doc_counts_by_country() -> list[dict]:
    docs_by_rec = defaultdict(list)
    for d in _all_docs_slim():
        docs_by_rec[d["record_id"]].append(d)
    out: dict[str, dict] = {}
    for r in _all_records_slim():
        o = out.setdefault(r["country"], {"iso3": r["country"], "eng_docs": 0, "ori_docs": 0})
        for d in docs_by_rec.get(r["id"], []):
            code = d["lang_code"]
            if code == "en" or (code is None and d["lang_type"] == "ENG"):
                o["eng_docs"] += 1
            if code is not None and code != "en":
                o["ori_docs"] += 1
    return list(out.values())


def list_policy_counts() -> list[dict]:
    out: dict[str, int] = defaultdict(int)
    for r in _all_records_slim():
        out[r["country"]] += 1
    return [{"iso3": k, "count": v} for k, v in out.items()]


def list_country_dashboard() -> list[dict]:
    """One row per country – the repository “Country Overview” table."""
    names = country_name_map()
    docs_by_rec = defaultdict(list)
    for d in _all_docs_slim():
        docs_by_rec[d["record_id"]].append(d)
    agg: dict[str, dict] = {}
    for r in _all_records_slim():
        a = agg.setdefault(r["country"], {
            "iso3": r["country"], "name": names.get(r["country"], r["country"]), "total_policies": 0,
            "records_with_docs": 0, "records_with_working_link": 0, "records_with_english": 0,
            "orig_non_english": False, "latest_update": None, "eng_docs": 0, "ori_docs": 0})
        a["total_policies"] += 1
        docs = docs_by_rec.get(r["id"], [])
        if docs:
            a["records_with_docs"] += 1
        if r["link_ok"] == 1:
            a["records_with_working_link"] += 1
        if any(d["lang_code"] == "en" for d in docs):
            a["records_with_english"] += 1
        for d in docs:
            if d["lang_type"] == "ORI" and d["lang_code"] and d["lang_code"] != "en":
                a["orig_non_english"] = True
            if d["lang_code"] == "en":
                a["eng_docs"] += 1
            if d["lang_code"] and d["lang_code"] != "en":
                a["ori_docs"] += 1
        u = r["updated_at"]
        if u and (a["latest_update"] is None or u > a["latest_update"]):
            a["latest_update"] = u
    for a in agg.values():
        a["latest_update"] = a["latest_update"].isoformat() if a["latest_update"] else ""
    return sorted(agg.values(), key=lambda a: a["name"].lower())


def get_language_breakdown() -> dict:
    names = country_name_map()
    rec_country = {r["id"]: r["country"] for r in _all_records_slim()}
    by: dict[str, dict] = {}
    for d in _all_docs_slim():
        iso = rec_country.get(d["record_id"])
        if not iso:
            continue
        a = by.setdefault(iso, {"iso3": iso, "name": names.get(iso, iso), "ori_total": 0, "ori_english": 0,
                                "ori_non_english": 0, "ori_unknown": 0, "eng_translations": 0})
        if d["lang_type"] == "ORI":
            a["ori_total"] += 1
            if d["lang_code"] == "en":
                a["ori_english"] += 1
            elif d["lang_code"]:
                a["ori_non_english"] += 1
            else:
                a["ori_unknown"] += 1
        elif d["lang_type"] == "ENG":
            a["eng_translations"] += 1
    by_country = sorted(by.values(), key=lambda a: a["name"].lower())
    overall = {k: sum(r[k] for r in by_country) for k in
               ("ori_total", "ori_english", "ori_non_english", "ori_unknown", "eng_translations")}
    return {"overall": overall, "by_country": by_country}


# ── per-country views ────────────────────────────────────────────────────────
def _attach_docs(records: list[dict]) -> list[dict]:
    if not records:
        return []
    clause, params = db.in_clause("r", [r["id"] for r in records])
    docs = [_doc(d) for d in db.query(f"SELECT * FROM {table('documents')} WHERE record_id IN {clause}", params)]
    dmap: dict[str, list[dict]] = defaultdict(list)
    for d in docs:
        dmap[d["record_id"]].append(d)
    return [{"record": r, "docs": dmap.get(r["id"], [])} for r in records]


def get_country_policies(iso3: str) -> list[dict]:
    """All records for a country grouped-ready: jurisdiction level → entity → tier → name."""
    rows = [_rec(r) for r in db.query(
        f"SELECT * FROM {table('policy_records')} WHERE country = :c "
        f"ORDER BY jurisdiction_level ASC, jurisdiction_name ASC NULLS FIRST, "
        f"policy_guidance_tier ASC NULLS LAST, LOWER(name_eng) ASC", {"c": iso3})]
    return _attach_docs(rows)


def get_records_with_docs_by_country(iso3: str) -> list[dict]:
    rows = [_rec(r) for r in db.query(
        f"SELECT * FROM {table('policy_records')} WHERE country = :c ORDER BY LOWER(name_eng) ASC", {"c": iso3})]
    return [x for x in _attach_docs(rows) if x["docs"]]


def get_country_docs_for_download(iso3: str, lang: str = "all") -> list[dict]:
    sql = (f"SELECT p.name_eng AS record_name, p.policy_guidance_tier AS tier, d.lang_type, d.lang_label, "
           f"d.file_name, d.file_size, d.file_path FROM {table('documents')} d "
           f"JOIN {table('policy_records')} p ON p.id = d.record_id WHERE p.country = :c")
    params = {"c": iso3}
    if lang in ("ENG", "ORI"):
        sql += " AND d.lang_type = :lt"
        params["lt"] = lang
    sql += " ORDER BY p.policy_guidance_tier ASC NULLS LAST, LOWER(p.name_eng) ASC"
    return db.query(sql, params)


def export_all_records_with_docs() -> list[dict]:
    names = country_name_map()
    recs = [_rec(r) for r in db.query(f"SELECT * FROM {table('policy_records')} ORDER BY country ASC, "
                                      f"LOWER(name_eng) ASC")]
    docs = [_doc(d) for d in db.query(f"SELECT * FROM {table('documents')}")]
    dmap: dict[str, list[dict]] = defaultdict(list)
    for d in docs:
        dmap[d["record_id"]].append(d)
    for r in recs:
        r["country_name"] = names.get(r["country"], r["country"])
        r["documents"] = dmap.get(r["id"], [])
    return recs


# ── documents ────────────────────────────────────────────────────────────────
def recompute_machine_readable(record_id: str) -> bool | None:
    flags = [d["text_layer"] for d in get_documents_for_record(record_id)]
    if any(f is False for f in flags):
        mr: bool | None = False
    elif flags and all(f is not None for f in flags):
        mr = True
    else:
        mr = None
    db.update("policy_records", record_id, {"machine_readable": mr})
    return mr


def upsert_document(record_id: str, lang_type: str, file_path: str, lang_code: str | None = None,
                    lang_label: str | None = None, file_name: str | None = None, file_size: int | None = None,
                    translation_source: str | None = None, text_layer: bool | None = None) -> dict:
    """Max one ENG + one ORI document per record (UC has no UNIQUE → select-then-write)."""
    source = translation_source if lang_type == "ENG" else None
    existing = db.query(f"SELECT id, file_path FROM {table('documents')} WHERE record_id = :r AND lang_type = :t",
                        {"r": record_id, "t": lang_type})
    values = dict(lang_code=lang_code, lang_label=lang_label, file_path=file_path, file_name=file_name,
                  file_size=file_size, translation_source=source, text_layer=text_layer, uploaded_at=db.now())
    if existing:
        doc_id = existing[0]["id"]
        db.update("documents", doc_id, values)
        replaced = existing[0]["file_path"]
    else:
        doc_id = db.uid()
        db.insert("documents", {"id": doc_id, "record_id": record_id, "lang_type": lang_type, **values})
        replaced = None
    recompute_machine_readable(record_id)
    doc = _doc(db.query(f"SELECT * FROM {table('documents')} WHERE id = :i", {"i": doc_id})[0])
    doc["_replaced_path"] = replaced if replaced and replaced != file_path else None
    return doc


def get_document(doc_id: str) -> dict | None:
    rows = db.query(f"SELECT * FROM {table('documents')} WHERE id = :i", {"i": doc_id})
    return _doc(rows[0]) if rows else None


def set_document_text_layer(doc_id: str, text_layer: bool | None) -> dict | None:
    db.update("documents", doc_id, {"text_layer": text_layer})
    doc = get_document(doc_id)
    if doc:
        recompute_machine_readable(doc["record_id"])
    return doc


def update_document_source(doc_id: str, translation_source: str | None) -> dict | None:
    db.update("documents", doc_id, {"translation_source": translation_source})
    return get_document(doc_id)


def delete_document(doc_id: str) -> dict | None:
    doc = get_document(doc_id)
    if not doc:
        return None
    db.execute(f"DELETE FROM {table('document_chunks')} WHERE document_id = :i", {"i": doc_id})
    db.delete("documents", doc_id)
    recompute_machine_readable(doc["record_id"])
    return doc


# ── map ──────────────────────────────────────────────────────────────────────
_LANG_FIX = {"BN": "Bengali", "CNR": "Montenegrin", "EN": "English"}
ENGLISH_ORIGINAL = {
    "AUS", "USA", "GBR", "CAN", "IRL", "NZL", "ZAF", "GHA", "KEN", "NGA", "ZMB", "ZWE", "UGA", "TZA", "MWI", "BWA",
    "NAM", "RWA", "SLE", "LBR", "GMB", "LSO", "SWZ", "MUS", "SYC", "IND", "PAK", "LKA", "SGP", "PHL", "PNG", "FJI",
    "JAM", "TTO", "BRB", "BHS", "BLZ", "GUY", "MLT", "BTN", "ETH", "SSD",
}


def _tidy_languages(labels: list[str]) -> str:
    seen: list[str] = []
    for raw in labels:
        for part in str(raw).split(","):
            s = part.strip()
            if not s:
                continue
            s = _LANG_FIX.get(s.upper(), s)
            if s not in seen:
                seen.append(s)
    return ", ".join(seen)


def get_map_countries() -> list[dict]:
    names = country_name_map()
    docs_by_rec = defaultdict(list)
    for d in _all_docs_slim():
        docs_by_rec[d["record_id"]].append(d)
    agg: dict[str, dict] = {}
    for r in _all_records_slim():
        a = agg.setdefault(r["country"], {"iso3": r["country"], "records": 0, "eng": 0, "ori": 0, "non_eng_ori": 0,
                                          "langs": []})
        a["records"] += 1
        for d in docs_by_rec.get(r["id"], []):
            if d["lang_type"] == "ENG":
                a["eng"] += 1
            if d["lang_type"] == "ORI":
                a["ori"] += 1
                if (d["lang_code"] or "").lower() not in ("en", "eng", ""):
                    a["non_eng_ori"] += 1
                    if d["lang_label"]:
                        a["langs"].append(d["lang_label"])
    out = []
    for iso3, a in agg.items():
        english = False if a["non_eng_ori"] > 0 else (True if a["ori"] > 0 else iso3 in ENGLISH_ORIGINAL)
        out.append({
            "iso3": iso3, "name": names.get(iso3, iso3), "records": a["records"], "eng_docs": a["eng"],
            "ori_docs": a["ori"],
            "original_language": "English" if english else (_tidy_languages(sorted(set(a["langs"]))) or "Non-English"),
            "status": "english" if english else "nonEnglish",
        })
    return sorted(out, key=lambda c: c["name"].lower())


# ── briefings cache (no UNIQUE constraint in UC → select-then-write) ─────────
def get_cached_briefing(brief_key: str, country: str, lang_type: str, scope: str = "all") -> dict | None:
    rows = db.query(
        f"SELECT content, generated_at FROM {table('briefings_cache')} WHERE brief_key = :k AND country = :c "
        f"AND lang_type = :l AND scope = :s", {"k": brief_key, "c": country, "l": lang_type, "s": scope})
    if not rows:
        return None
    return {"content": rows[0]["content"], "generated_at": to_dt(rows[0]["generated_at"])}


def upsert_cached_briefing(brief_key: str, country: str, lang_type: str, content: str, scope: str = "all") -> datetime:
    ts = db.now()
    rows = db.query(
        f"SELECT id FROM {table('briefings_cache')} WHERE brief_key = :k AND country = :c AND lang_type = :l "
        f"AND scope = :s", {"k": brief_key, "c": country, "l": lang_type, "s": scope})
    if rows:
        db.update("briefings_cache", rows[0]["id"], {"content": content, "generated_at": ts})
    else:
        db.insert("briefings_cache", {"id": db.uid(), "brief_key": brief_key, "country": country,
                                      "lang_type": lang_type, "scope": scope, "content": content,
                                      "generated_at": ts})
    return ts


# ── stats used by pages ──────────────────────────────────────────────────────
def home_stats() -> dict:
    records = _all_records_slim()
    counts = list_doc_counts_by_country()
    return {
        "countries": len({r["country"] for r in records}),
        "records": len(records),
        "eng": sum(c["eng_docs"] for c in counts),
        "ori": sum(c["ori_docs"] for c in counts),
    }


def get_country_records_with_documents(iso3: str) -> list[dict]:
    """Every record of a country with its `documents` list inline (shape of the original export API;
    records without documents are included with an empty list)."""
    name = country_name_map().get(iso3, iso3)
    recs = [_rec(r) for r in db.query(
        f"SELECT * FROM {table('policy_records')} WHERE country = :c ORDER BY LOWER(name_eng) ASC", {"c": iso3})]
    if not recs:
        return []
    clause, params = db.in_clause("r", [r["id"] for r in recs])
    dmap: dict[str, list[dict]] = defaultdict(list)
    for d in db.query(f"SELECT * FROM {table('documents')} WHERE record_id IN {clause}", params):
        dmap[d["record_id"]].append(_doc(d))
    for r in recs:
        r["country_name"] = name
        r["documents"] = dmap.get(r["id"], [])
    return recs


def list_jurisdictions() -> list[dict]:
    """Distinct sub-national entities with CURRENT records (drives the briefing scope selector)."""
    rows = db.query(
        f"SELECT DISTINCT country, jurisdiction_name AS name FROM {table('policy_records')} "
        f"WHERE jurisdiction_level = 'subnational' AND jurisdiction_name IS NOT NULL AND jurisdiction_name <> '' "
        f"AND record_status = 'latest' ORDER BY country, name")
    return [{"country": r["country"], "name": r["name"]} for r in rows]
