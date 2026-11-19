"""
Flask routes living next to the Dash app (they share the same server / session) – ports of `web/app/api/**`.

    GET  /api/countries[?withRecords=1]              lookup list
    GET  /api/records[?search=&country=&…]           list, GET /api/records/<id>, GET /api/records/language-breakdown
    GET  /api/jurisdictions                          sub-national entities with current records
    GET/POST /api/briefings                          cached AI briefs (POST needs a signed-in user)
    GET  /api/export/documents[?since=&lang=]        corpus export for downstream pipelines (CORS + rate limited)
    GET  /api/countries/<ISO3>/changes               policy-change report (json | md)
    GET  /api/imf-pima[?country=]  /api/imf-pima/export  GET/POST /api/imf-pima/refresh
    /api/v1/{countries,records,records/<id>,coach/query,openapi.json}   partner API (X-API-Key)
    GET  /api/health

Document files are never served: they are only uploaded to the UC volume.  All writes (records, documents,
batch imports) happen in Dash callbacks, so there are no POST/PATCH/DELETE record endpoints.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime
from decimal import Decimal
from functools import wraps

from flask import Blueprint, Response, abort, jsonify, request

import constants as C
from utils import api_keys, auth, briefings, chat, mai_client, queries
from utils.ratelimit import rate_limit, rate_limit_state
from utils.routes import api_url

log = logging.getLogger(__name__)
bp = Blueprint("pim_api", __name__)

UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)


# ── helpers ──────────────────────────────────────────────────────────────────
def _iso(o):
    if isinstance(o, (datetime, date)):
        return o.isoformat()
    if isinstance(o, Decimal):
        return float(o)
    raise TypeError(f"not serialisable: {type(o)}")


def _json(data, status: int = 200, headers: dict | None = None) -> Response:
    resp = Response(json.dumps(data, default=_iso, ensure_ascii=False), status=status, mimetype="application/json")
    for k, v in (headers or {}).items():
        resp.headers[k] = v
    return resp


def _err(msg: str, status: int) -> Response:
    return _json({"error": msg}, status)


def _origin() -> str:
    proto = request.headers.get("X-Forwarded-Proto", request.scheme).split(",")[0].strip()
    host = request.headers.get("X-Forwarded-Host", request.host).split(",")[0].strip()
    return f"{proto}://{host}"


def _camel(key: str) -> str:
    head, *rest = key.split("_")
    return head + "".join(p.title() for p in rest)


def _camelize(o):
    if isinstance(o, dict):
        return {_camel(k): _camelize(v) for k, v in o.items()}
    if isinstance(o, list):
        return [_camelize(x) for x in o]
    return o


def _doc_out(d: dict, camel: bool = False) -> dict:
    """Public view of a document row (never leaks the internal storage key)."""
    out = {k: v for k, v in d.items() if k not in ("file_path", "blob_url")}
    return _camelize(out) if camel else out


def _record_out(r: dict, camel: bool = True) -> dict:
    out = dict(r)
    if "documents" in out:
        out["documents"] = [_doc_out(d) for d in out["documents"]]
    return _camelize(out) if camel else out


def _int(v, default=None):
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


# ── lookups / records (read-only) ────────────────────────────────────────────
@bp.get("/api/countries")
def countries():
    with_records = request.args.get("withRecords") == "1"
    data = queries.list_countries() if with_records else queries.list_all_countries()
    return _json(data, headers={"Cache-Control": "public, max-age=300"})


@bp.get("/api/records")
def records_list():
    a = request.args
    limit = _int(a.get("limit"))
    recs = queries.list_records(
        search=a.get("search") or None, country=a.get("country") or None,
        policy_tier=_int(a.get("policyGuidanceTier")), strategy_tier=_int(a.get("strategyTier")),
        limit=min(limit, 500) if limit and limit > 0 else 500, offset=max(0, _int(a.get("offset"), 0)))
    return _json([_record_out(r) for r in recs], headers={"Cache-Control": "public, max-age=60"})


@bp.get("/api/records/language-breakdown")
def language_breakdown():
    return _json(_camelize(queries.get_language_breakdown()), headers={"Cache-Control": "public, max-age=60"})


@bp.get("/api/records/<record_id>")
def record_get(record_id: str):
    rec = queries.get_record_with_docs(record_id)
    if not rec:
        return _err("Record not found", 404)
    return _json(_record_out(rec), headers={"Cache-Control": "public, max-age=60"})


@bp.get("/api/jurisdictions")
def jurisdictions():
    return _json(queries.list_jurisdictions(), headers={"Cache-Control": "public, max-age=300"})


# ── briefings cache ──────────────────────────────────────────────────────────
@bp.get("/api/briefings")
def briefings_get():
    a = request.args
    key, country = a.get("key", ""), (a.get("country") or "").upper()
    lang, scope = (a.get("lang") or "ENG").upper(), (a.get("scope") or "all").strip()
    if not briefings.valid_params(key, country, lang, scope):
        return _err("Invalid parameters", 400)
    cached = queries.get_cached_briefing(key, country, lang, scope)
    if not cached:
        return Response(status=204)
    return _json({"content": cached["content"], "generatedAt": cached["generated_at"]},
                 headers={"Cache-Control": "no-store"})


@bp.post("/api/briefings")
def briefings_post():
    user = auth.current_user()
    if not user:
        return _err("Unauthorized", 401)
    b = request.get_json(silent=True)
    if not isinstance(b, dict):
        return _err("Invalid JSON", 400)
    key, country = str(b.get("key", "")), str(b.get("country", "")).upper()
    lang, scope = str(b.get("lang", "ENG")).upper(), str(b.get("scope", "all")).strip()
    content = b.get("content") if isinstance(b.get("content"), str) else ""
    if not briefings.valid_params(key, country, lang, scope) or len(content) < briefings.MIN_CONTENT:
        return _err("Invalid payload", 400)
    if len(content) > briefings.MAX_CONTENT:
        return _err("Content too large", 413)
    ts = queries.upsert_cached_briefing(key, country, lang, content, scope)
    return _json({"ok": True, "generatedAt": ts})


# ── export for downstream pipelines ─────────────────────────────────────────
_ALLOWED_ORIGINS = [o.strip() for o in (C.EXPORT_ALLOWED_ORIGINS or "").split(",") if o.strip()]


def _cors() -> dict:
    origin = request.headers.get("Origin")
    allowed = origin if origin and any(origin.startswith(o) for o in _ALLOWED_ORIGINS) else (
        _ALLOWED_ORIGINS[0] if _ALLOWED_ORIGINS else "*")
    return {"Access-Control-Allow-Origin": allowed, "Access-Control-Allow-Methods": "GET, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type"}


@bp.route("/api/export/documents", methods=["GET", "OPTIONS"])
def export_documents():
    if request.method == "OPTIONS":
        return Response(status=204, headers=_cors())
    ok, retry = rate_limit(f"export:{auth.client_ip() or 'unknown'}", 10, 60)
    if not ok:
        return _json({"error": "Rate limit exceeded"}, 429, {"Retry-After": str(retry), **_cors()})
    data = queries.export_all_records_with_docs()
    since = request.args.get("since")
    if since:
        try:
            dt = datetime.fromisoformat(since.replace("Z", "+00:00")).replace(tzinfo=None)
            data = [r for r in data if r["updated_at"] and r["updated_at"].replace(tzinfo=None) > dt]
        except ValueError:
            pass
    lang = (request.args.get("lang") or "").upper()
    if lang in ("ENG", "ORI"):
        data = [{**r, "documents": [d for d in r["documents"] if d["lang_type"] == lang]} for r in data]
        data = [r for r in data if r["documents"]]
    payload = {"count": len(data), "exportedAt": datetime.utcnow().isoformat() + "Z", "lang": lang or "ALL",
               "records": [_record_out(r) for r in data]}
    return _json(payload, headers={"Cache-Control": "public, max-age=300", **_cors()})


# ── policy changes ──────────────────────────────────────────────────────────
@bp.get("/api/countries/<iso3>/changes")
def country_changes(iso3: str):
    from utils import changes  # noqa: PLC0415  (module is only needed here and on the Policy changes page)

    iso3 = iso3.upper()
    q = request.args
    mode, since, fmt = (q.get("mode") or "latest").lower(), q.get("since"), (q.get("format") or "json").lower()
    if not re.fullmatch(r"[A-Z]{3}", iso3):
        return _err("Invalid country code", 400)
    if mode not in ("latest", "since"):
        return _err("mode must be latest or since", 400)
    if mode == "since":
        try:
            ok = bool(since) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", since) and date.fromisoformat(since)
        except ValueError:
            ok = False
        if not ok or since < "1990-01-01" or since > date.today().isoformat():
            return _err("since must be a date between 1990-01-01 and today (YYYY-MM-DD)", 400)
    if fmt not in ("json", "md"):
        return _err("format must be json or md", 400)
    report = changes.get_policy_changes(iso3, mode, since)
    if not report:
        return _err("No profile found for this country", 404)
    if fmt == "md":
        base = _origin() + api_url("/").rstrip("/")  # app root incl. the Posit Connect content path
        return Response(changes.render_markdown(report, base), mimetype="text/markdown",
                        headers={"Content-Disposition": f'attachment; filename="{changes.download_name(report, since)}"',
                                 "Cache-Control": "no-store"})
    return _json(report, headers={"Cache-Control": "public, max-age=300"})


# ── IMF PIMA ────────────────────────────────────────────────────────────────
def _pima_shape(s: dict) -> dict:
    from utils import countries_data  # noqa: PLC0415

    yn = lambda b: "Y" if b else "N"  # noqa: E731
    return {
        "iso3": s["iso3"], "name": countries_data.resolve_country_name(s["iso3"]),
        "pima": yn(s["pima_count"] > 0), "pimaRepeat": yn(s["pima_count"] > 1),
        "pimaYear": s["first_year"], "pimaRepeatYear": s["repeat_year"],
        "pima1Status": s["first_status"], "pima1Published": yn(s["first_published"]),
        "pima1ReportUrl": s["first_report_url"], "pima1ReferenceUrl": s["first_reference_url"],
        "pima2Status": s["repeat_status"], "pima2Published": yn(s["repeat_published"]),
        "pima2ReportUrl": s["repeat_report_url"], "pima2ReferenceUrl": s["repeat_reference_url"],
        "cpima": yn(s["cpima_year"] is not None), "cpimaYear": s["cpima_year"], "cpimaStatus": s["cpima_status"],
        "cpimaPublished": yn(s["cpima_published"]), "cpimaReportUrl": s["cpima_report_url"],
        "cpimaReferenceUrl": s["cpima_reference_url"], "imfCountryPage": s["source_url"],
        "lastChecked": s["fetched_at"], "assessments": [_camelize(a) for a in s["assessments"]],
    }


@bp.get("/api/imf-pima")
def imf_pima_get():
    from utils import imf_pima  # noqa: PLC0415

    cache = {"Cache-Control": "public, max-age=3600"}
    country = (request.args.get("country") or "").upper()
    if country:
        if not re.fullmatch(r"[A-Z]{3}", country):
            return _err("Invalid country code", 400)
        s = imf_pima.get_summary(country)
        if not s:
            return _err("No IMF PIMA assessment listed for this country", 404)
        return _json(_pima_shape(s), headers=cache)
    rows = sorted((_pima_shape(s) for s in imf_pima.list_summaries().values()), key=lambda c: c["name"])
    last = max((c["lastChecked"] for c in rows), default="")
    return _json({"source": imf_pima.IMF_PIMA_ABOUT_URL, "lastChecked": last, "count": len(rows), "countries": rows},
                 headers=cache)


@bp.get("/api/imf-pima/export")
def imf_pima_export():
    from utils import xlsx_exports  # noqa: PLC0415

    data, name = xlsx_exports.imf_pima_workbook()
    return Response(data, headers={"Content-Disposition": f'attachment; filename="{name}"', "Cache-Control": "no-store",
                                   "Content-Type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"})


@bp.route("/api/imf-pima/refresh", methods=["GET", "POST"])
def imf_pima_refresh():
    from utils import imf_pima  # noqa: PLC0415

    force = request.method == "POST"
    if force:
        if not auth.current_user():
            return _err("Unauthorized", 401)
    elif C.CRON_SECRET and request.headers.get("Authorization") != f"Bearer {C.CRON_SECRET}":
        return _err("Unauthorized", 401)
    try:
        last = imf_pima.last_fetch_at()
        # anonymous (cron GET) calls are throttled to one refresh per 12 h, like the original
        if not force and last and (datetime.utcnow() - last.replace(tzinfo=None)).total_seconds() < 12 * 3600:
            return _json({"skipped": True, "reason": "refreshed recently", "lastChecked": last.isoformat()})
        return _json(imf_pima.refresh(), headers={"Cache-Control": "no-store"})
    except Exception:  # noqa: BLE001
        log.exception("IMF PIMA refresh failed")
        return _err("Refresh failed", 500)


# ── partner API v1 ──────────────────────────────────────────────────────────
def _v1_error(status: int, code: str, message: str, headers: dict | None = None) -> Response:
    return _json({"error": {"code": code, "message": message}}, status, headers)


def _v1_disabled() -> Response:
    return _v1_error(503, "NotAvailable", "The partner API is not available on this deployment.")


def with_api_key(fn):
    """X-API-Key (sha256 lookup) + per-partner rate limit, like `withApiKey()` in lib/api-auth.ts.
    Answers 503 while the partner API is switched off (constants.PARTNER_API_ENABLED – no keys table yet)."""

    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not C.PARTNER_API_ENABLED:
            return _v1_disabled()
        partner = api_keys.validate_api_key(request.headers.get("X-API-Key"))
        if not partner:
            return _v1_error(401, "Unauthorized", "Missing or invalid X-API-Key.")
        limit = partner["rate_limit_per_minute"]
        ok, retry, remaining, reset = rate_limit_state(f"partner:{partner['id']}", limit, 60)
        rl = {"X-RateLimit-Limit": str(limit), "X-RateLimit-Remaining": str(remaining), "X-RateLimit-Reset": str(reset)}
        if not ok:
            return _v1_error(429, "RateLimited",
                             f"Rate limit exceeded for partner '{partner['key_prefix']}…'. Retry after {retry}s.",
                             {**rl, "Retry-After": str(retry), "X-RateLimit-Remaining": "0"})
        try:
            resp = fn(partner, *args, **kwargs)
        except Exception as exc:  # noqa: BLE001
            log.exception("api/v1 handler failed")
            return _v1_error(500, "InternalError", str(exc) or "Internal error")
        for k, v in rl.items():
            resp.headers[k] = v
        return resp

    return wrapper


@bp.get("/api/v1/countries")
@with_api_key
def v1_countries(_partner):
    rows = [{"iso3": c["iso3"], "name": c["name"], "gdp_2024_bn": c.get("gdp_2024_bn"),
             "pub_inv_pct_gdp": c.get("pub_inv_pct_gdp"), "pub_inv_2024_bn": c.get("pub_inv_2024_bn")}
            for c in queries.list_countries_with_context()]
    return _json({"data": rows, "count": len(rows)})


_V1_RECORD_FIELDS = ["id", "country", "name_eng", "name_orig", "year", "year_revised", "source", "overview",
                     "policy_guidance_tier", "strategy_tier", "jurisdiction_level", "jurisdiction_name", "record_status",
                     "superseded_by_link", "superseded_by_note", "link", "pages", "updated_at"]


@bp.get("/api/v1/records")
@with_api_key
def v1_records(_partner):
    sp = request.args
    where, params = [], {}

    def int_arg(name: str, lo=None, hi=None):
        v = sp.get(name)
        if v is None:
            return None, None
        n = _int(v)
        if n is None or (lo is not None and not lo <= n <= hi):
            return None, f"{name} must be {'1, 2, or 3' if lo else 'an integer'}."
        return n, None

    country = (sp.get("country") or "").upper().strip()
    if country:
        if not re.fullmatch(r"[A-Z]{3}", country):
            return _v1_error(400, "BadRequest", "country must be an ISO-3 code (3 letters).")
        where.append("country = :country")
        params["country"] = country
    for name, col, lo, hi in (("tier", "policy_guidance_tier", 1, 3), ("strategy", "strategy_tier", 1, 3),
                              ("year_from", "year", None, None), ("year_to", "year", None, None)):
        n, e = int_arg(name, lo, hi)
        if e:
            return _v1_error(400, "BadRequest", e)
        if n is not None:
            op = ">=" if name == "year_from" else "<=" if name == "year_to" else "="
            where.append(f"{col} {op} :{name}")
            params[name] = n
    status = sp.get("status")
    if status is not None:
        if status not in ("latest", "archived"):
            return _v1_error(400, "BadRequest", "status must be 'latest' or 'archived'.")
        where.append("record_status = :status")
        params["status"] = status
    search = (sp.get("search") or "").strip()
    if search:
        where.append("(LOWER(name_eng) LIKE :s OR LOWER(COALESCE(overview,'')) LIKE :s)")
        params["s"] = f"%{search.lower()}%"
    limit = min(200, max(1, _int(sp.get("limit"), 50) or 50))
    offset = max(0, _int(sp.get("offset"), 0) or 0)
    rows = queries.v1_list_records(where, params, limit, offset)
    data = [{k: r.get(k) for k in _V1_RECORD_FIELDS} for r in rows]
    return _json({"data": data, "count": len(data), "limit": limit, "offset": offset, "has_more": len(data) == limit})


@bp.get("/api/v1/records/<record_id>")
@with_api_key
def v1_record(_partner, record_id: str):
    if not UUID_RE.match(record_id):
        return _v1_error(400, "BadRequest", "Record id must be a UUID.")
    rec = queries.get_record_with_docs(record_id)
    if not rec:
        return _v1_error(404, "NotFound", f"No record with id {record_id}.")
    data = {k: rec.get(k) for k in ("id", "country", "name_eng", "name_orig", "year", "year_revised", "source",
                                    "overview", "policy_guidance_tier", "strategy_tier", "link", "pages", "updated_at")}
    data["documents"] = [{"id": d["id"], "lang_type": d["lang_type"], "lang_code": d["lang_code"],
                          "lang_label": d["lang_label"], "file_name": d["file_name"], "file_size": d["file_size"]} for d in rec["documents"]]
    return _json({"data": data})


@bp.post("/api/v1/coach/query")
@with_api_key
def v1_coach(_partner):
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return _v1_error(400, "BadRequest", "Request body must be valid JSON.")
    question = body.get("question")
    if not isinstance(question, str) or not question.strip():
        return _v1_error(400, "BadRequest", "`question` must be a non-empty string.")
    country = body.get("country")
    if country is not None and (not isinstance(country, str) or not re.fullmatch(r"[A-Z]{3}", country)):
        return _v1_error(400, "BadRequest", "`country` must be an ISO-3 code (3 letters).")
    history = body.get("chat_history")
    if history is not None and not isinstance(history, list):
        return _v1_error(400, "BadRequest", "`chat_history` must be an array.")
    try:
        out = chat.ask(question, history or [], {"country": country} if country else None)
    except mai_client.MaiError as exc:
        return _v1_error(502, "UpstreamError", str(exc))
    return _json(out)


@bp.get("/api/v1/openapi.json")
def v1_openapi():
    """Public (no key) so the Redoc viewer on the API page – and partners' own viewers – can load it."""
    if not C.PARTNER_API_ENABLED:
        return _v1_disabled()
    with open(C.OPENAPI_JSON, encoding="utf-8") as fh:
        spec = fh.read().replace("{{ORIGIN}}", _origin() + api_url("/").rstrip("/"))
    return Response(spec, mimetype="application/json",
                    headers={"Cache-Control": "public, max-age=300", "Access-Control-Allow-Origin": "*"})


@bp.get("/api/health")
def health():
    return _json({"status": "ok", "mai": mai_client.describe() if mai_client.is_configured() else "not configured"})


def register(server) -> None:
    server.register_blueprint(bp)
