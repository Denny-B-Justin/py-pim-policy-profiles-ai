"""
PIM Policy Changes – port of `web/modules/changes/{types,queries}.ts` and `web/lib/policy-changes-report.ts`.

A "change" is detected from two kinds of evidence held in the repository:
  * instrument-level: the year an instrument was issued (`year`) or revised (`year_revised`), refined with an exact
    date when one can be read from the record's source line; archived records count as "superseded" when their
    successor falls in the period;
  * profile-level: when a record was added to, or updated in, the country profile (created_at / updated_at).
    Consecutive activity days form one "profile update".

The report is a plain dict with the same camelCase keys the Next.js app returned, so it can be sent as JSON by
`/api/countries/<iso3>/changes` and rendered by the Dash page unchanged.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime, timedelta, timezone

import constants as C
from utils import db
from utils.db import table
from utils.routes import href

CHANGE_KINDS = ["new_instrument", "revised_instrument", "superseded", "added_to_profile", "updated_in_profile"]
CHANGE_KIND_LABEL = {
    "new_instrument": "New instrument",
    "revised_instrument": "Revised",
    "superseded": "Superseded",
    "added_to_profile": "Added to profile",
    "updated_in_profile": "Profile record updated",
}
_KIND_RANK = {k: i for i, k in enumerate(CHANGE_KINDS)}


def empty_counts() -> dict[str, int]:
    return {k: 0 for k in CHANGE_KINDS}


# ── tier labels (lib/tiers.ts) ───────────────────────────────────────────────
def get_tier_label(tier: int | None) -> str | None:
    if tier is None:
        return None
    return next((t["label"] for t in C.TIERS if t["value"] == tier), None)


def get_strategy_label(value: int | None) -> str | None:
    if value is None:
        return None
    return next((s["label"] for s in C.STRATEGY_TYPES if s["value"] == value), None)


# ── date helpers ─────────────────────────────────────────────────────────────
_MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6, "july": 7, "august": 8,
    "september": 9, "october": 10, "november": 11, "december": 12,
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6, "jul": 7, "aug": 8, "sep": 9, "sept": 9, "oct": 10, "nov": 11,
    "dec": 12,
}
_RX_DMY_TEXT = re.compile(r"\b(\d{1,2})\s+([A-Za-z]{3,9})\.?\s+(\d{4})\b", re.A)  # 21 November 2025 / 21 Nov 2025
_RX_MDY_TEXT = re.compile(r"\b([A-Za-z]{3,9})\.?\s+(\d{1,2}),\s+(\d{4})\b", re.A)  # November 21, 2025
_RX_ISO = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b", re.A)  # 2025-11-21
_RX_DMY_NUM = re.compile(r"\b(\d{1,2})[./](\d{1,2})[./](\d{4})\b", re.A)  # 21.11.2025 / 21/11/2025


def _iso(y: int, m: int, d: int) -> str | None:
    if m < 1 or m > 12 or d < 1 or d > 31:
        return None
    try:
        return date(y, m, d).isoformat()
    except ValueError:
        return None


def extract_dates(text: str | None) -> list[str]:
    """Every calendar date that can be read from a free-text source line (ISO strings, de-duplicated)."""
    if not text:
        return []
    out: list[str] = []
    for m in _RX_DMY_TEXT.finditer(text):
        mo = _MONTHS.get(m.group(2).lower())
        if mo and (d := _iso(int(m.group(3)), mo, int(m.group(1)))):
            out.append(d)
    for m in _RX_MDY_TEXT.finditer(text):
        mo = _MONTHS.get(m.group(1).lower())
        if mo and (d := _iso(int(m.group(3)), mo, int(m.group(2)))):
            out.append(d)
    for m in _RX_ISO.finditer(text):
        if d := _iso(int(m.group(1)), int(m.group(2)), int(m.group(3))):
            out.append(d)
    for m in _RX_DMY_NUM.finditer(text):
        if d := _iso(int(m.group(3)), int(m.group(2)), int(m.group(1))):
            out.append(d)
    return list(dict.fromkeys(out))


def instrument_date_for(text: str | None, latest_year: int | None) -> str | None:
    """The exact instrument date: the earliest date in the record's name or source line that falls in the
    instrument's latest year (revision year if any, else issue year) – typically the enactment/issue date rather
    than a later entry-into-force or adoption date."""
    if not latest_year:
        return None
    in_year = sorted(d for d in extract_dates(text) if int(d[:4]) == latest_year)
    return in_year[0] if in_year else None


def _day_of(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d")


def _add_days(iso_day: str, n: int) -> str:
    return (date.fromisoformat(iso_day) + timedelta(days=n)).isoformat()


def _days_between(a: str, b: str) -> int:
    return (date.fromisoformat(a) - date.fromisoformat(b)).days


def _ts(dt: datetime) -> str:
    """JS `Date.toISOString()` – UTC, millisecond precision, trailing Z."""
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def now_utc() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def fmt_date(iso_like: str) -> str:
    """'9 November 2026' (en-GB, UTC) from an ISO date or timestamp string."""
    d = date.fromisoformat(str(iso_like)[:10])
    return f"{d.day} {d:%B} {d.year}"


def _collate(s: str) -> str:
    """Approximation of `String.prototype.localeCompare` ordering (accent- and case-insensitive first)."""
    base = unicodedata.normalize("NFD", s or "")
    return "".join(ch for ch in base if not unicodedata.combining(ch)).casefold()


# ── profile updates (clusters of activity days) ──────────────────────────────
def cluster_updates(rows: list[dict], gap_days: int = 2) -> list[dict]:
    """Group activity days that are at most `gap_days` apart into profile updates."""
    days = sorted({_day_of(r["created_at"]) for r in rows} | {_day_of(r["updated_at"]) for r in rows})
    clusters: list[dict] = []
    for d in days:
        last = clusters[-1] if clusters else None
        if last and _days_between(d, last["to"]) <= gap_days:
            last["to"] = d
        else:
            clusters.append({"from": d, "to": d})

    out = []
    for c in clusters:
        def in_c(dt: datetime, c=c) -> bool:
            return c["from"] <= _day_of(dt) <= c["to"]

        created = [r for r in rows if in_c(r["created_at"])]
        updated = [r for r in rows if not in_c(r["created_at"]) and in_c(r["updated_at"])]
        out.append({
            "from": c["from"], "to": c["to"], "created": len(created), "updated": len(updated),
            "archived": sum(1 for r in created + updated if r["record_status"] == "archived"),
        })
    return out


# ── data access ──────────────────────────────────────────────────────────────
_COLS = ("id, name_eng, name_orig, year, year_revised, source, policy_guidance_tier, strategy_tier, "
         "jurisdiction_level, jurisdiction_name, record_status, superseded_by_link, superseded_by_note, link, "
         "created_at, updated_at")


def _load(iso3: str) -> tuple[str | None, list[dict], list[dict]]:
    from utils.queries import _rec  # noqa: PLC0415  (shared row normalisation)

    c = db.query(f"SELECT name FROM {table('countries')} WHERE iso3 = :i", {"i": iso3})
    rows = [_rec(r) for r in db.query(f"SELECT {_COLS} FROM {table('policy_records')} WHERE country = :i", {"i": iso3})]
    for r in rows:  # timestamps are NOT NULL in the schema; stay defensive for hand-migrated data
        r["created_at"] = r["created_at"] or r["updated_at"] or datetime(1970, 1, 1)
        r["updated_at"] = r["updated_at"] or r["created_at"]
    docs: list[dict] = []
    if rows:
        ph, params = db.in_clause("r", [r["id"] for r in rows])
        docs = db.query(f"SELECT record_id, lang_type FROM {table('documents')} WHERE record_id IN {ph}", params)
    return (c[0]["name"] if c else None), rows, docs


# ── main entry point ─────────────────────────────────────────────────────────
def get_policy_changes(iso3: str, mode: str, since: str | None = None) -> dict | None:
    iso3 = iso3.upper()
    country_name, rows, doc_rows = _load(iso3)
    if not rows and country_name is None:
        return None
    country_name = country_name or iso3

    docs_by_record: dict[str, set[str]] = {}
    for d in doc_rows:
        docs_by_record.setdefault(d["record_id"], set()).add(d["lang_type"])

    updates = cluster_updates(rows)
    latest_update = updates[-1] if updates else None
    previous_update = updates[-2] if len(updates) > 1 else None

    # ── window ──
    notes: list[str] = []
    if mode == "latest":
        if not latest_update:
            return None
        frm = latest_update["from"] + "T00:00:00.000Z"
        to = _add_days(latest_update["to"], 1) + "T00:00:00.000Z"
        since_year = int(previous_update["to"][:4]) if previous_update else int(latest_update["from"][:4])
        label = (f"profile update of {fmt_date(latest_update['from'])}" if latest_update["from"] == latest_update["to"]
                 else f"profile update of {fmt_date(latest_update['from'])} – {fmt_date(latest_update['to'])}")
        notes.append(
            f"Compared with the previous profile update of {fmt_date(previous_update['to'])}: instruments issued or "
            f"revised from {since_year} onwards are flagged as new or revised." if previous_update else
            f"This is the first profile update for {country_name}: every record was added in it; instruments issued "
            f"or revised in {since_year} are flagged as new or revised.")
    else:
        if not (since and re.fullmatch(r"\d{4}-\d{2}-\d{2}", since)):
            raise ValueError("A valid since date (YYYY-MM-DD) is required")
        frm = since + "T00:00:00.000Z"
        to = _ts(now_utc())
        since_year = int(since[:4])
        label = f"since {fmt_date(since)}"
        if since[5:] != "01-01":
            notes.append(
                f"Instrument dates are recorded by year; an instrument from {since_year} is included unless an exact "
                f"date before {fmt_date(since)} could be read from its source line.")
    from_dt = datetime.strptime(frm[:10], "%Y-%m-%d")
    to_dt_ = datetime.strptime(to[:19], "%Y-%m-%dT%H:%M:%S")
    since_day = frm[:10]

    def in_window(d: datetime) -> bool:
        return from_dt <= d < to_dt_

    def qualifies(year: int | None, exact: str | None) -> bool:
        """Does a year (with an optional exact date) fall in the period?"""
        if year is None:
            return False
        if year > since_year:
            return True
        if year < since_year:
            return False
        return exact is None or exact >= since_day

    by_id = {r["id"]: r for r in rows}

    # ── classify ──
    items: list[dict] = []
    for r in rows:
        latest_year = max(r["year"] or 0, r["year_revised"] or 0) or None
        exact = instrument_date_for(f"{r['name_eng']} {r['source'] or ''}", latest_year)
        kinds: list[str] = []

        link = r["superseded_by_link"]
        succ_id = link[len("/records/"):] if link and link.startswith("/records/") else None
        succ = by_id.get(succ_id) if succ_id else None
        successor = ({"id": succ["id"], "nameEng": succ["name_eng"], "year": succ["year"],
                      "yearRevised": succ["year_revised"]} if succ else None)

        if r["record_status"] == "archived":
            # superseded in the period when the successor was issued/revised in it
            # (or, failing a linked successor, when the curator note names a year in it)
            succ_year = (max(succ["year"] or 0, succ["year_revised"] or 0) or None) if succ else None
            succ_exact = (instrument_date_for(f"{succ['name_eng']} {succ['source'] or ''}", succ_year)
                          if succ else None)
            note_years = ([int(y) for y in re.findall(r"\b(?:19|20)\d{2}\b", r["superseded_by_note"] or "", re.A)]
                          if not succ else [])
            note_year = max(note_years) if note_years else None
            in_period = qualifies(succ_year, succ_exact) if succ else qualifies(note_year, None)
            if in_period:
                kinds.append("superseded")
        elif r["year_revised"] is not None and r["year_revised"] > (r["year"] or 0):
            if qualifies(r["year_revised"], exact):
                kinds.append("revised_instrument")
        elif qualifies(r["year"], exact):
            kinds.append("new_instrument")

        if in_window(r["created_at"]):
            kinds.append("added_to_profile")
        elif in_window(r["updated_at"]):
            kinds.append("updated_in_profile")

        # latest mode lists what the update touched; since mode lists instrument-level changes
        if mode == "latest":
            include = "added_to_profile" in kinds or "updated_in_profile" in kinds
        else:
            include = any(k in ("new_instrument", "revised_instrument", "superseded") for k in kinds)
        if not include:
            continue

        langs = docs_by_record.get(r["id"], set())
        items.append({
            "id": r["id"], "nameEng": r["name_eng"], "nameOrig": r["name_orig"], "year": r["year"],
            "yearRevised": r["year_revised"], "latestYear": latest_year, "instrumentDate": exact,
            "datePrecision": "exact" if exact else ("year" if latest_year else "none"),
            "source": r["source"], "policyGuidanceTier": r["policy_guidance_tier"], "strategyTier": r["strategy_tier"],
            "jurisdictionLevel": r["jurisdiction_level"], "jurisdictionName": r["jurisdiction_name"],
            "recordStatus": r["record_status"], "supersededByLink": r["superseded_by_link"],
            "supersededByNote": r["superseded_by_note"], "successor": successor, "link": r["link"],
            "hasEnglish": "ENG" in langs, "hasOriginal": "ORI" in langs,
            "createdAt": _ts(r["created_at"]), "updatedAt": _ts(r["updated_at"]), "kinds": kinds,
        })

    # ── group by policy-guidance tier ──
    def rank(it: dict) -> int:
        return min(_KIND_RANK[k] for k in it["kinds"])

    items.sort(key=lambda it: (it["policyGuidanceTier"] if it["policyGuidanceTier"] is not None else 9, rank(it),
                               -(it["latestYear"] or 0), _collate(it["nameEng"])))
    groups: list[dict] = []
    for it in items:
        g = next((x for x in groups if x["tier"] == it["policyGuidanceTier"]), None)
        if g is None:
            g = {"tier": it["policyGuidanceTier"], "label": get_tier_label(it["policyGuidanceTier"]) or "Unclassified",
                 "items": [], "counts": empty_counts()}
            groups.append(g)
        g["items"].append(it)
        for k in it["kinds"]:
            g["counts"][k] += 1
    counts = empty_counts()
    for g in groups:
        for k in CHANGE_KINDS:
            counts[k] += g["counts"][k]

    notes.append(
        "Change types: new instrument = issued in the period; revised = amended or consolidated in the period (year "
        "revised); superseded = archived record whose successor falls in the period; added to profile / profile "
        "record updated = repository activity.")
    notes.append(
        "Dates come from the record's year and year-revised fields, refined with an exact date where the source line "
        "states one; profile activity comes from the repository's created/updated timestamps.")

    return {
        "country": {"iso3": iso3, "name": country_name},
        "mode": mode,
        "window": {"from": frm, "to": to, "sinceYear": since_year, "label": label},
        "profile": {
            "records": len(rows),
            "current": sum(1 for r in rows if r["record_status"] != "archived"),
            "archived": sum(1 for r in rows if r["record_status"] == "archived"),
            "documents": len(doc_rows),
        },
        "updates": updates,
        "latestUpdate": latest_update,
        "previousUpdate": previous_update,
        "groups": groups,
        "counts": counts,
        "totalChanges": len(items),
        "notes": notes,
        "generatedAt": _ts(now_utc()),
    }


# ── report rendering (lib/policy-changes-report.ts) ──────────────────────────
def report_title(r: dict) -> str:
    return f"PIM Policy Changes — {r['country']['name']} ({r['window']['label']})"


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def summary_text(r: dict) -> str:
    """One-paragraph plain-language summary of the report."""
    c = r["counts"]
    parts: list[str] = []
    if c["new_instrument"]:
        parts.append(_plural(c["new_instrument"], "new instrument"))
    if c["revised_instrument"]:
        parts.append(f"{c['revised_instrument']} revised")
    if c["superseded"]:
        parts.append(f"{c['superseded']} superseded")
    policy = ", ".join(parts) if parts else "no instrument-level changes"
    tiers = ", ".join(f"{len(g['items'])} in {re.sub(r' — .*', '', g['label'])}" for g in r["groups"] if g["items"])
    name, prof = r["country"]["name"], r["profile"]
    if r["mode"] == "latest":
        u = r["latestUpdate"]
        when = fmt_date(u["from"]) if u["from"] == u["to"] else f"{fmt_date(u['from'])} – {fmt_date(u['to'])}"
        prev = (f"the previous update of {fmt_date(r['previousUpdate']['to'])}" if r["previousUpdate"]
                else "no earlier version of the profile")
        return (f"The {name} profile was last updated on {when}: {_plural(u['created'], 'record')} added"
                f"{', ' + str(u['updated']) + ' updated' if u['updated'] else ''}"
                f"{', ' + str(u['archived']) + ' archived as superseded' if u['archived'] else ''}"
                f" ({tiers or 'none'}), against {prev}. Among the records touched, {policy} date from "
                f"{r['window']['sinceYear']} onwards."
                f" The profile now holds {prof['records']} records ({prof['current']} current, {prof['archived']} "
                f"archived) and {prof['documents']} documents.")
    n = r["totalChanges"]
    last = fmt_date(r["latestUpdate"]["to"]) if r["latestUpdate"] else "—"
    return (f"{'No' if n == 0 else n} change{'' if n == 1 else 's'} to {name}'s public investment management framework "
            f"{r['window']['label']} {'was' if n == 1 else 'were'} identified in the profile: {policy}"
            f"{' (' + tiers + ')' if tiers else ''}. The profile holds {prof['records']} records "
            f"({prof['current']} current, {prof['archived']} archived), last updated on {last}.")


def year_text(it: dict) -> str | None:
    if not it["year"]:
        return None
    rev = f" · rev. {it['yearRevised']}" if it["yearRevised"] and it["yearRevised"] != it["year"] else ""
    return f"{it['year']}{rev}"


def _record_url(base: str, rid: str) -> str:
    return f"{base}/{href('record', id=rid)}"


def _item_line(it: dict, base: str) -> str:
    when = fmt_date(it["instrumentDate"]) if it["instrumentDate"] else (year_text(it) or "n.d.")
    kinds = ", ".join(CHANGE_KIND_LABEL[k] for k in it["kinds"])
    strat = f" · {get_strategy_label(it['strategyTier'])}" if it["strategyTier"] else ""
    juris = (f" · {it['jurisdictionName']}"
             if it["jurisdictionLevel"] != "national" and it["jurisdictionName"] else "")
    if it["successor"]:
        succ = f" Superseded by: [{it['successor']['nameEng']}]({_record_url(base, it['successor']['id'])})."
    elif it["supersededByNote"]:
        succ = f" {it['supersededByNote']}"
    else:
        succ = ""
    links = " · ".join(x for x in (f"[profile record]({_record_url(base, it['id'])})",
                                   f"[original source]({it['link']})" if it["link"] else None) if x)
    return (f"- **{it['nameEng']}**{' (*' + it['nameOrig'] + '*)' if it['nameOrig'] else ''} — {when} — {kinds}{strat}"
            f"{juris}.{' ' + it['source'] + '.' if it['source'] else ''}{succ} {links}")


def render_markdown(r: dict, base: str = "") -> str:
    """Markdown rendering of the report.  `base` is the app's root URL without a trailing slash
    (e.g. "https://connect.example.com/content/<guid>"); record links become `<base>/?page=record&id=<id>`."""
    base = (base or "").rstrip("/")
    lines: list[str] = [f"# {report_title(r)}", ""]
    lines += [
        f"Country: **{r['country']['name']}** ({r['country']['iso3']}) · Mode: "
        f"**{'latest profile update' if r['mode'] == 'latest' else 'changes since a date'}** · "
        f"Period: **{r['window']['label']}** · Generated {fmt_date(r['generatedAt'])}.", ""]
    lines += ["## Summary", "", summary_text(r), ""]
    lines.append("| Tier | " + " | ".join(CHANGE_KIND_LABEL[k] for k in CHANGE_KINDS) + " | Records |")
    lines.append("|---|" + "|".join("---:" for _ in CHANGE_KINDS) + "|---:|")
    for g in r["groups"]:
        lines.append(f"| {g['label']} | " + " | ".join(str(g["counts"][k]) for k in CHANGE_KINDS) + f" | {len(g['items'])} |")
    lines += ["| **Total** | " + " | ".join(f"**{r['counts'][k]}**" for k in CHANGE_KINDS) + f" | **{r['totalChanges']}** |", ""]

    for g in r["groups"]:
        lines += [f"## {g['label']} ({len(g['items'])})", ""]
        lines += [_item_line(it, base) for it in g["items"]]
        lines.append("")
    if not r["groups"]:
        lines += ["_No changes identified for this period._", ""]

    lines += ["## Profile updates", ""]
    if not r["updates"]:
        lines.append("_No profile updates recorded._")
    for u in reversed(r["updates"]):
        when = fmt_date(u["from"]) if u["from"] == u["to"] else f"{fmt_date(u['from'])} – {fmt_date(u['to'])}"
        lines.append(f"- {when}: {u['created']} added{', ' + str(u['updated']) + ' updated' if u['updated'] else ''}"
                     f"{', ' + str(u['archived']) + ' archived' if u['archived'] else ''}")
    lines += ["", "## Notes", ""]
    lines += [f"- {n}" for n in r["notes"]]
    lines += ["", f"_Source: PIM Policy Profiles AI repository, {base}/{href('country', iso3=r['country']['iso3'])}._"]
    return "\n".join(lines)


def download_name(r: dict, since: str | None = None, ext: str = "md") -> str:
    stamp = f"since_{since}" if r["mode"] == "since" else f"update_{(r.get('latestUpdate') or {}).get('to') or ''}"
    return f"PIM_Policy_Changes_{r['country']['iso3']}_{stamp}.{ext}"
