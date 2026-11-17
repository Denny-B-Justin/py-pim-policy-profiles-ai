"""
IMF PIMA data: parsing the IMF Infrastructure Governance portal, the `policy_repo_imf_pima_assessments` table, the
curated references, and the per-country summaries used by the Countries page and the public API.

Port of `web/lib/imf-pima.ts` + `web/modules/pima/queries.ts`.  (Upserts are select-then-write because
Unity Catalog tables have no UNIQUE constraints.)
"""

from __future__ import annotations

import html as _html
import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from typing import Any, Callable

import requests

import constants as C
from utils import db
from utils.countries_data import countries_data
from utils.db import table

log = logging.getLogger(__name__)

IMF_PIMA_BASE = C.IMF_PIMA_BASE
IMF_PIMA_LIST_URL = C.IMF_PIMA_LIST_URL
IMF_PIMA_ABOUT_URL = C.IMF_PIMA_ABOUT_URL
PIMA_TYPES = ("PIMA", "PIMA Update")
_UA = ("Mozilla/5.0 (compatible; PIM-Policy-Profiles-AI/1.0) AppleWebKit/537.36 (KHTML, like Gecko) "
       "Chrome/128.0 Safari/537.36")


# ── html helpers ─────────────────────────────────────────────────────────────
def strip_tags(s: str) -> str:
    return re.sub(r"\s+", " ", _html.unescape(re.sub(r"<[^>]+>", " ", s))).strip()


_MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
           "november", "december"]


def month_year_to_date(text: str) -> str | None:
    m = re.search(r"\b([A-Za-z]+)\.?\s+(\d{4})\b", text)
    if not m:
        return None
    key = m.group(1).lower()[:3]
    for i, name in enumerate(_MONTHS):
        if name.startswith(key):
            return f"{m.group(2)}-{i + 1:02d}-01"
    return None


def fetch_text(url: str, timeout: int = 30) -> str:
    r = requests.get(url, headers={"User-Agent": _UA, "Accept": "text/html,*/*"}, timeout=timeout)
    if r.status_code >= 400:
        raise RuntimeError(f"HTTP {r.status_code} for {url}")
    return r.text


# ── country list / names ─────────────────────────────────────────────────────
def parse_country_list(html: str) -> list[dict]:
    out = []
    rx = re.compile(r'<div class="country-list__card ([^"]*)">[\s\S]*?<div class="country-list__title">\s*'
                    r'<a href="([^"]+)">([^<]+)</a>')
    for m in rx.finditer(html):
        cls, href = m.group(1), m.group(2)
        out.append({
            "name": _html.unescape(m.group(3)).strip(),
            "slug": re.sub(r"\.html$", "", href.split("/")[-1]),
            "url": href if href.startswith("http") else IMF_PIMA_BASE + href,
            "categories": {
                "public": bool(re.search(r"\b0-public\b", cls)), "nonpublic": bool(re.search(r"\b1-nonpublic\b", cls)),
                "upcoming": bool(re.search(r"\b2-upcoming\b", cls)), "pfram": bool(re.search(r"\b3-ppppfram\b", cls)),
                "climate": bool(re.search(r"\b4-climatechange\b", cls)),
            },
        })
    return out


_ALIASES = {
    "afghanistan, islamic republic of": "AFG", "egypt, arab republic of": "EGY", "cote d'ivoire": "CIV",
    "côte d'ivoire": "CIV", "democratic republic of congo": "COD", "democratic republic of the congo": "COD",
    "republic of congo": "COG", "the gambia": "GMB", "gambia, the": "GMB", "kyrgyz republic": "KGZ",
    "slovak republic": "SVK", "st. vincent and the grenadines": "VCT", "saint vincent and the grenadines": "VCT",
    "russian federation": "RUS", "brunei": "BRN", "brunei darussalam": "BRN", "türkiye": "TUR", "turkiye": "TUR",
    "lao p.d.r.": "LAO", "lao pdr": "LAO", "korea": "KOR", "korea, republic of": "KOR", "vietnam": "VNM",
    "viet nam": "VNM", "north macedonia": "MKD", "bosnia and herzegovina": "BIH", "sao tome and principe": "STP",
    "são tomé and príncipe": "STP", "micronesia": "FSM", "bahamas, the": "BHS", "the bahamas": "BHS",
    "czech republic": "CZE", "eswatini": "SWZ", "cabo verde": "CPV", "timor-leste": "TLS", "kosovo": "XKX",
    "moldova": "MDA", "iran, islamic republic of": "IRN", "venezuela, república bolivariana de": "VEN",
    "tanzania": "TZA", "united republic of tanzania": "TZA",
}


def imf_name_to_iso3(name: str) -> str | None:
    n = re.sub(r"\s+", " ", name.lower()).strip()
    if n in _ALIASES:
        return _ALIASES[n]
    for c in countries_data():
        if c["name"].lower() == n:
            return c["iso3"]
    return None


# ── country page ─────────────────────────────────────────────────────────────
def _classify(title: str) -> str | None:
    t = title.lower()
    if re.search(r"climate\s*pima|c-pima", t):
        return "C-PIMA"
    if re.search(r"pima\s*update", t):
        return "PIMA Update"
    if re.search(r"follow[- ]?up[^|]*\bpima\b|\bpima\b[^|]*follow[- ]?up", t):
        return "PIMA follow-up"
    if re.search(r"public investment management assessment\s*\(pima\)", t) or \
            re.search(r"^pima\b(?!\s*(training|handbook|workshop|seminar))", t):
        return "PIMA"
    return None


def _status_from_title(title: str, card: dict) -> str:
    if re.search(r"\bunpublished\b", title, re.I):
        return "unpublished"
    if re.search(r"\bpublished\b", title, re.I):
        return "published"
    cat = card["categories"]
    if cat["nonpublic"] and not cat["public"]:
        return "unpublished"
    if cat["public"] and not cat["nonpublic"]:
        return "published"
    return "unknown"


def _doc_types(label: str) -> list[str]:
    s = label.lower()
    types = []
    if re.search(r"c-?pima", s):
        types.append("C-PIMA")
    if "update" in s:
        types.append("PIMA Update")
    if re.search(r"follow[- ]?up", s):
        types.append("PIMA follow-up")
    return types or ["PIMA"]


def parse_country_page(html: str, card: dict) -> list[dict]:
    out: list[dict] = []
    tbl = re.search(r'class="engagements-list"[\s\S]*?</table>', html)
    if tbl:
        for m in re.finditer(r"<tr[^>]*>\s*<td[^>]*>([\s\S]*?)</td>\s*<td[^>]*>([\s\S]*?)</td>\s*</tr>", tbl.group(0)):
            title = strip_tags(m.group(1))
            typ = _classify(title)
            if not typ:
                continue
            md = month_year_to_date(strip_tags(m.group(2)))
            if not md:
                continue
            out.append({"assessment_type": typ, "title": title, "status": _status_from_title(title, card),
                        "mission_date": md, "year": int(md[:4]), "published_at": None, "report_url": None,
                        "summary_url": None})

    def latest_of(t: str):
        c = sorted((a for a in out if a["assessment_type"] == t), key=lambda a: a["mission_date"], reverse=True)
        return c[0] if c else None

    def norm(x: str) -> str:
        return re.sub(r"[^a-z]", "", x.lower())

    for m in re.finditer(r'<a[^>]+href="([^"]+\.pdf[^"]*)"[^>]*>([\s\S]*?)</a>', html, re.I):
        href = m.group(1)
        if not re.search(r"/content/dam/PIMA/", href, re.I):
            continue
        fm = re.search(r"/Countries/([^/]+)/", href, re.I)
        if fm and norm(fm.group(1)) not in (norm(card["slug"]), norm(card["name"])):
            continue
        label = strip_tags(m.group(2))
        url = href if href.startswith("http") else IMF_PIMA_BASE + href
        for typ in _doc_types(f"{href.split('/')[-1]} {label}"):
            target = latest_of(typ)
            if not target:
                continue
            if re.search(r"summary", label, re.I) or re.search(r"_summary", href, re.I):
                target["summary_url"] = target["summary_url"] or url
                if re.search(r"high[- ]level summary", label, re.I) and target["status"] != "published":
                    target["status"] = "summary"
            else:
                target["report_url"] = target["report_url"] or url
                target["status"] = "published"
    for m in re.finditer(r"Summary of ([A-Za-z -]*?)\s*report[\s\S]{0,400}?\(Published in ([A-Za-z]+\s+\d{4})\)", html, re.I):
        for typ in _doc_types(m.group(1)):
            target = latest_of(typ)
            if target:
                target["published_at"] = target["published_at"] or month_year_to_date(m.group(2))
                target["status"] = "published"
    return sorted(out, key=lambda a: a["mission_date"])


def fetch_imf_pima(concurrency: int = 4, log_fn: Callable[[str], None] | None = None) -> tuple[list[dict], list[dict]]:
    say = log_fn or (lambda s: None)
    cards = parse_country_list(fetch_text(IMF_PIMA_LIST_URL))
    say(f"IMF list: {len(cards)} countries")

    def one(card: dict) -> dict:
        iso3 = imf_name_to_iso3(card["name"])
        try:
            return {"card": card, "iso3": iso3, "assessments": parse_country_page(fetch_text(card["url"]), card)}
        except Exception as exc:  # noqa: BLE001
            say(f"  ! {card['name']}: {exc}")
            return {"card": card, "iso3": iso3, "assessments": [], "error": str(exc)}

    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
        results = list(pool.map(one, cards))
    results.sort(key=lambda r: r["card"]["name"])
    return cards, results


# ── persistence ──────────────────────────────────────────────────────────────
def _d(v: Any) -> str | None:
    if v is None:
        return None
    if isinstance(v, (date, datetime)):
        return v.strftime("%Y-%m-%d")
    return str(v)[:10]


def _rows() -> list[dict]:
    return db.query(f"SELECT * FROM {table('imf_pima_assessments')}")


def upsert_assessments(iso3: str, imf_country_name: str, source_url: str, parsed: list[dict]) -> dict:
    inserted = updated = removed = 0
    keep: set[str] = set()
    ts = db.now()
    existing = db.query(f"SELECT id, assessment_type, mission_date, source FROM {table('imf_pima_assessments')} "
                        f"WHERE iso3 = :i", {"i": iso3})
    by_key = {(r["assessment_type"], _d(r["mission_date"])): r for r in existing}
    for a in parsed:
        keep.add(f"{a['assessment_type']}|{a['mission_date']}")
        values = {"title": a["title"], "status": a["status"], "year": a["year"], "published_at": a["published_at"],
                  "report_url": a["report_url"], "summary_url": a["summary_url"], "source_url": source_url,
                  "imf_country_name": imf_country_name, "fetched_at": ts, "updated_at": ts}
        hit = by_key.get((a["assessment_type"], a["mission_date"]))
        if hit:
            db.update("imf_pima_assessments", hit["id"], values)
            updated += 1
        else:
            db.insert("imf_pima_assessments", {"id": db.uid(), "iso3": iso3, "assessment_type": a["assessment_type"],
                                               "mission_date": a["mission_date"], "source": "portal",
                                               "reference_url": None, "note": None, "created_at": ts, **values})
            inserted += 1
    for r in existing:
        if r["source"] == "portal" and f"{r['assessment_type']}|{_d(r['mission_date'])}" not in keep:
            db.delete("imf_pima_assessments", r["id"])
            removed += 1
    return {"inserted": inserted, "updated": updated, "removed": removed}


def apply_references(log_fn: Callable[[str], None] | None = None) -> dict:
    """Curated references (data/imf-pima-references.json). Mentions → summaries → full reports last."""
    with open(C.IMF_PIMA_REFERENCES_JSON, encoding="utf-8") as fh:
        refs = json.load(fh)["references"]
    order = {"mention": 0, "hls": 1, "tar": 2}
    refs = sorted(refs, key=lambda r: order[r["kind"]])
    applied = created = 0
    for ref in refs:
        month = f"{ref['year']}-{int(ref['month']):02d}-01"
        is_report = ref["kind"] == "tar"
        rows = db.query(f"SELECT * FROM {table('imf_pima_assessments')} WHERE iso3 = :i", {"i": ref["iso3"]})
        cand = [r for r in rows if r["assessment_type"] == ref["type"] and abs(int(r["year"]) - ref["year"]) <= 1]
        cand.sort(key=lambda r: (abs(int(r["year"]) - ref["year"]), [-ord(c) for c in _d(r["mission_date"]) or ""]))
        match = cand[0] if cand else None
        ts = db.now()
        if match:
            status = "published" if (is_report or match["status"] == "published") else "summary"
            values = {"reference_url": ref["url"], "status": status, "note": ref.get("note") or match["note"],
                      "report_url": match["report_url"] or ((ref.get("pdf") or ref["url"]) if is_report else None),
                      "published_at": (_d(match["published_at"]) or month) if is_report else _d(match["published_at"]),
                      "updated_at": ts}
            if any(match[k] != values[k] for k in ("reference_url", "status", "note", "report_url")):
                db.update("imf_pima_assessments", match["id"], values)
            applied += 1
        else:
            exist = db.query(f"SELECT id, report_url FROM {table('imf_pima_assessments')} WHERE iso3 = :i AND "
                             f"assessment_type = :t AND mission_date = :m",
                             {"i": ref["iso3"], "t": ref["type"], "m": month})
            if exist:
                upd = {"title": ref["title"], "reference_url": ref["url"], "note": ref.get("note"), "updated_at": ts}
                if is_report:
                    upd.update(report_url=exist[0]["report_url"] or (ref.get("pdf") or ref["url"]), status="published")
                db.update("imf_pima_assessments", exist[0]["id"], upd)
            else:
                db.insert("imf_pima_assessments", {
                    "id": db.uid(), "iso3": ref["iso3"], "assessment_type": ref["type"], "title": ref["title"],
                    "status": "published" if is_report else "summary", "mission_date": month, "year": ref["year"],
                    "published_at": None if ref["kind"] == "mention" else month,
                    "report_url": (ref.get("pdf") or ref["url"]) if is_report else None,
                    "summary_url": ref["url"] if ref["kind"] == "hls" else None, "source_url": ref["url"],
                    "imf_country_name": None, "source": "manual" if ref["kind"] == "mention" else "imf.org",
                    "reference_url": ref["url"], "note": ref.get("note"), "fetched_at": ts, "created_at": ts,
                    "updated_at": ts})
            created += 1
            if log_fn:
                log_fn(f"  {ref['iso3']} {ref['type']} {ref['year']}: reference created ({ref['kind']})")
    return {"applied": applied, "created": created}


def refresh(log_fn: Callable[[str], None] | None = None) -> dict:
    """Full refresh from the IMF site (used by the cron script and the Countries page)."""
    started = datetime.utcnow().isoformat() + "Z"
    cards, results = fetch_imf_pima(4, log_fn)
    known = {c["iso3"] for c in db.query(f"SELECT iso3 FROM {table('countries')}")}
    stats = {"started_at": started, "finished_at": "", "countries_listed": len(cards), "matched": 0, "unmatched": [],
             "fetch_errors": [], "skipped_no_country_row": [], "rows_inserted": 0, "rows_updated": 0,
             "rows_removed": 0, "references_applied": 0, "references_created": 0}
    for r in results:
        if r.get("error"):
            stats["fetch_errors"].append({"country": r["card"]["name"], "error": r["error"]}); continue
        if not r["iso3"]:
            stats["unmatched"].append(r["card"]["name"]); continue
        if r["iso3"] not in known:
            stats["skipped_no_country_row"].append(f"{r['card']['name']} ({r['iso3']})"); continue
        stats["matched"] += 1
        u = upsert_assessments(r["iso3"], r["card"]["name"], r["card"]["url"], r["assessments"])
        stats["rows_inserted"] += u["inserted"]; stats["rows_updated"] += u["updated"]; stats["rows_removed"] += u["removed"]
        if log_fn:
            log_fn(f"  {r['iso3']} {r['card']['name']}: {len(r['assessments'])} assessment(s) "
                   f"(+{u['inserted']} ~{u['updated']} -{u['removed']})")
    ref = apply_references(log_fn)
    stats["references_applied"], stats["references_created"] = ref["applied"], ref["created"]
    stats["finished_at"] = datetime.utcnow().isoformat() + "Z"
    db.invalidate("imf_pima_assessments")
    return stats


# ── summaries ────────────────────────────────────────────────────────────────
def _summarise(iso3: str, rows: list[dict]) -> dict:
    rows = sorted(rows, key=lambda r: _d(r["mission_date"]) or "")
    views = [{"assessment_type": r["assessment_type"], "title": r["title"], "status": r["status"],
              "year": int(r["year"]), "mission_date": _d(r["mission_date"]), "published_at": _d(r["published_at"]),
              "report_url": r["report_url"], "summary_url": r["summary_url"], "reference_url": r["reference_url"],
              "source": r["source"], "note": r["note"]} for r in rows]
    pimas = [v for v in views if v["assessment_type"] in PIMA_TYPES]
    first = pimas[0] if pimas else None
    repeat = pimas[-1] if len(pimas) > 1 else None
    cpima = next((v for v in reversed(views) if v["assessment_type"] == "C-PIMA"), None)
    fetched = max(filter(None, (db.to_dt(r.get("fetched_at")) for r in rows)), default=None)
    g = lambda v, k: v[k] if v else None  # noqa: E731
    return {
        "iso3": iso3, "source_url": rows[0]["source_url"] if rows else "",
        "fetched_at": fetched.isoformat() if fetched else "", "assessments": views, "pima_count": len(pimas),
        "first_year": g(first, "year"), "first_status": g(first, "status"),
        "first_published": bool(first and first["status"] == "published"),
        "first_report_url": g(first, "report_url"), "first_reference_url": g(first, "reference_url"),
        "repeat_year": g(repeat, "year"), "repeat_status": g(repeat, "status"),
        "repeat_published": bool(repeat and repeat["status"] == "published"),
        "repeat_report_url": g(repeat, "report_url"), "repeat_reference_url": g(repeat, "reference_url"),
        "cpima_year": g(cpima, "year"), "cpima_status": g(cpima, "status"),
        "cpima_published": bool(cpima and cpima["status"] == "published"),
        "cpima_report_url": g(cpima, "report_url"), "cpima_reference_url": g(cpima, "reference_url"),
    }


def list_summaries() -> dict[str, dict]:
    def load():
        by: dict[str, list[dict]] = {}
        for r in _rows():
            by.setdefault(r["iso3"], []).append(r)
        return {iso3: _summarise(iso3, rs) for iso3, rs in by.items()}
    return db.cached("imf_pima_assessments:summaries", C.QUERY_CACHE_SECONDS * 5, load)


def get_summary(iso3: str) -> dict | None:
    return list_summaries().get(iso3)


def last_fetch_at() -> datetime | None:
    vals = [db.to_dt(r["fetched_at"]) for r in db.query(f"SELECT MAX(fetched_at) AS fetched_at FROM {table('imf_pima_assessments')}")]
    return vals[0] if vals and vals[0] else None
