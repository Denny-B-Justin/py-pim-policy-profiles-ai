"""Excel exports – port of `app/api/imf-pima/export/route.ts` (ExcelJS → openpyxl)."""

from __future__ import annotations

import io
from datetime import datetime, timezone

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from utils import imf_pima, queries
from utils.countries_data import countries_data

STATUS_LABEL = {
    "published": "Published (full report)",
    "summary": "Summary / reference only",
    "unpublished": "Conducted, not published",
    "unknown": "Conducted (publication unknown)",
}
HEADER_FILL = PatternFill("solid", fgColor="FFE8F0FE")


def _yn(b) -> str:
    return "Y" if b else "N"


def _fmt_date(d) -> str:
    if not d:
        return ""
    if isinstance(d, str):
        d = datetime.fromisoformat(d.replace("Z", "+00:00"))
    return f"{d.day} {d:%b} {d.year}"


README = [
    ("Country / ISO3", "Country name and ISO 3166-1 alpha-3 code.", "Repository country table (196 countries)."),
    ("Policies in repository", "Number of PIM policy records held for the country in this repository.",
     "Count of policy records per country."),
    ("IMF PIMA (Y/N)", "Y when the IMF has conducted at least one Public Investment Management Assessment (PIMA or "
     "PIMA Update) in the country, or refers to one in an IMF publication.",
     "Y if any assessment of type PIMA or PIMA Update exists for the country (any status); N otherwise."),
    ("IMF PIMA Repeat (Y/N)", "Y when a second (or later) PIMA / PIMA Update mission is listed.",
     "Y if two or more PIMA / PIMA Update assessments exist."),
    ("IMF PIMA Year", "Year of the first PIMA mission.",
     "Year of the earliest PIMA / PIMA Update mission date; for curated references the publication / reference year."),
    ("IMF PIMA Repeat Year", "Year of the latest repeat PIMA.",
     "Year of the most recent PIMA / PIMA Update when more than one exists."),
    ("IMF PIMA 1 / 2 Status", "Publication status of the first / latest repeat PIMA: Published (full report), Summary / "
     "reference only, Conducted not published, Conducted (publication unknown).",
     "Published when the IMF portal labels the mission 'Published' or links the full Technical Assistance Report; "
     "Summary / reference only when there is only an IMF High-Level Summary report or a mention in another IMF "
     "publication (grey star); Conducted, not published when the portal labels it 'Unpublished'; otherwise unknown."),
    ("IMF PIMA 1 / 2 Published (Y/N)", "Whether the full report can be downloaded.",
     "Y only when the status is Published (full Technical Assistance Report available)."),
    ("IMF PIMA 1 / 2 Report URL", "Direct link to the full report (PDF) where published.",
     "PDF linked under 'Related Documents' on the IMF PIMA country page."),
    ("IMF PIMA 1 / 2 Reference URL", "Link to the IMF High-Level Summary report or the IMF publication that refers to "
     "the assessment.", "From the curated reference list (data/imf-pima-references.json), matched to the mission by "
     "country, type and year (±1)."),
    ("C-PIMA (Y/N), Year, Status, URLs", "Same information for the Climate PIMA module (latest C-PIMA mission).",
     "Assessments of type C-PIMA."),
    ("PIMA missions (count)", "Number of PIMA and PIMA Update missions listed.",
     "Count of assessments of type PIMA or PIMA Update."),
    ("IMF PIMA country page", "The country's page on the IMF Infrastructure Governance portal.",
     "infrastructuregovern.imf.org/content/PIMA/Home/Region-and-Country-Information/Countries/<Country>.html; blank "
     "when the IMF lists no page (e.g. Lesotho)."),
    ("Last checked", "When the IMF data for the country was last fetched.",
     "fetched_at of the country's rows; the refresh runs monthly (scripts/cron_refresh_pima.py) and can be run on "
     "demand by a signed-in user."),
    ("Assessments sheet", "One row per mission: type (PIMA, PIMA Update, C-PIMA, PIMA follow-up), title as shown by "
     "the IMF, mission month, status, publication month, report / summary / reference links, source and notes.",
     "Source 'portal' = parsed from the IMF PIMA portal engagement table; 'imf.org' = IMF High-Level Summary Technical "
     "Assistance Report; 'manual' = curated reference (e.g. a PIMA mentioned in an Article IV staff report)."),
    ("Markers on the Countries page", "Solid green circle = PIMA with full report published; hollow green circle = "
     "PIMA conducted, not published; grey star = IMF reference or high-level summary only; blue diamond = Climate "
     "PIMA (solid = published).", "Derived from the status of each assessment; one marker per mission, oldest first."),
    ("IMF publications outside the portal", "PIMA reports and references published on imf.org that the portal does not "
     "list (full Technical Assistance Reports, High-Level Summary reports, mentions in Article IV staff reports) are "
     "captured through a curated reference list (data/imf-pima-references.json). A full report sets the status "
     "Published and links the PDF; a high-level summary or a mention sets Summary / reference only.",
     "imf.org blocks automated crawling (its publication lists are rendered client-side and bulk requests are "
     "refused), so the list is reviewed periodically with the IMF Technical Assistance Reports, High-Level Summary and "
     "Country Reports listings and applied on every monthly refresh. Example: Kingdom of Lesotho PIMA (Technical "
     "Assistance Report, 17 July 2026) — no portal page."),
    ("Known gaps", "Missions dated 'Upcoming' on the portal are not stored; Anguilla and Cook Islands are not in the "
     "country table; PIMA mentions buried in other IMF documents are only captured when added to the reference list.", ""),
    ("Sources", f"IMF Infrastructure Governance portal — {imf_pima.IMF_PIMA_ABOUT_URL} (country list: "
     f"{imf_pima.IMF_PIMA_LIST_URL}); IMF Technical Assistance Reports — "
     "https://www.imf.org/en/publications/technical-assistance-reports; IMF High-Level Summary Technical Assistance "
     "Reports — https://www.imf.org/en/publications/high-level-summary-technical-assistance-reports; IMF Country "
     "Reports (Article IV) — https://www.imf.org/en/publications/country-reports", ""),
]

COUNTRY_HEADERS = [
    "ISO3", "Country", "Policies in repository",
    "IMF PIMA (Y/N)", "IMF PIMA Repeat (Y/N)", "IMF PIMA Year", "IMF PIMA Repeat Year",
    "IMF PIMA 1 Status", "IMF PIMA 1 Published (Y/N)", "IMF PIMA 1 Report URL", "IMF PIMA 1 Reference URL",
    "IMF PIMA 2 Status", "IMF PIMA 2 Published (Y/N)", "IMF PIMA 2 Report URL", "IMF PIMA 2 Reference URL",
    "C-PIMA (Y/N)", "C-PIMA Year", "C-PIMA Status", "C-PIMA Published (Y/N)", "C-PIMA Report URL",
    "C-PIMA Reference URL", "PIMA missions (count)", "IMF PIMA country page", "Last checked",
]
ASSESSMENT_HEADERS = ["ISO3", "Country", "Type", "Title (IMF)", "Mission month", "Year", "Status", "Published month",
                      "Report URL", "Summary URL", "Reference URL", "Source", "Note", "IMF country page", "Last checked"]


def _country_row(c: dict, s: dict | None, policies: int) -> list:
    st = lambda x: STATUS_LABEL.get(x, x) if x else ""  # noqa: E731
    g = lambda k, d=None: (s or {}).get(k, d)  # noqa: E731
    return [
        c["iso3"], c["name"], policies,
        _yn(s and g("pima_count", 0) > 0), _yn(s and g("pima_count", 0) > 1), g("first_year"), g("repeat_year"),
        st(g("first_status")), _yn(g("first_published")), g("first_report_url") or "", g("first_reference_url") or "",
        st(g("repeat_status")), _yn(g("repeat_published")), g("repeat_report_url") or "", g("repeat_reference_url") or "",
        _yn(s and g("cpima_year") is not None), g("cpima_year"), st(g("cpima_status")), _yn(g("cpima_published")),
        g("cpima_report_url") or "", g("cpima_reference_url") or "",
        g("pima_count", 0), g("source_url") or "", _fmt_date(g("fetched_at")) if s else "",
    ]


def _header(ws, headers, widths):
    ws.append(headers)
    for c in ws[1]:
        c.font = Font(bold=True)
        c.fill = HEADER_FILL
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = w
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:{get_column_letter(len(headers))}1"


def imf_pima_workbook() -> tuple[bytes, str]:
    """→ (xlsx bytes, file name)."""
    summaries = imf_pima.list_summaries()
    counts = {r["iso3"]: r["count"] for r in queries.list_policy_counts()}
    countries = countries_data()
    generated = datetime.now(timezone.utc)
    last_checked = max((s["fetched_at"] for s in summaries.values() if s.get("fetched_at")), default="")

    wb = Workbook()
    rm = wb.active
    rm.title = "ReadMe"
    for col, w in zip("ABC", (34, 70, 90)):
        rm.column_dimensions[col].width = w
    rm.append(["IMF PIMA status — PIM Policy Profiles AI"])
    rm["A1"].font = Font(bold=True, size=14)
    rm.append([f"Generated {_fmt_date(generated)}; IMF data last checked "
               f"{_fmt_date(last_checked) if last_checked else '—'}. Source: IMF Infrastructure Governance portal and "
               f"IMF publications (see Sources)."])
    rm.append([f"Countries with at least one IMF assessment: "
               f"{sum(1 for s in summaries.values() if s['assessments'])}; countries in the table: {len(countries)}."])
    rm.append([])
    rm.append(["Variable", "Definition", "How it is generated / source"])
    for c in rm[5]:
        c.font = Font(bold=True)
        c.fill = HEADER_FILL
    for row in README:
        rm.append(list(row))
        for c in rm[rm.max_row]:
            c.alignment = Alignment(vertical="top", wrap_text=True)

    ws = wb.create_sheet("Countries")
    _header(ws, COUNTRY_HEADERS, [60 if ("URL" in h or "page" in h) else 22 if len(h) > 18 else 14 for h in COUNTRY_HEADERS])
    for c in countries:
        ws.append(_country_row(c, summaries.get(c["iso3"]), counts.get(c["iso3"], 0)))

    wa = wb.create_sheet("Assessments")
    _header(wa, ASSESSMENT_HEADERS, [60 if any(k in h for k in ("URL", "page", "Title", "Note")) else 16
                                      for h in ASSESSMENT_HEADERS])
    names = {c["iso3"]: c["name"] for c in countries}
    for s in sorted(summaries.values(), key=lambda x: names.get(x["iso3"], x["iso3"])):
        for a in s["assessments"]:
            wa.append([s["iso3"], names.get(s["iso3"], s["iso3"]), a["assessment_type"], a["title"],
                       (a["mission_date"] or "")[:7], a["year"], STATUS_LABEL.get(a["status"], a["status"]),
                       (a["published_at"] or "")[:7], a["report_url"] or "", a["summary_url"] or "",
                       a["reference_url"] or "", a["source"], a["note"] or "", s["source_url"],
                       _fmt_date(s["fetched_at"])])

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue(), f"IMF_PIMA_status_{generated:%Y-%m-%d}.xlsx"
