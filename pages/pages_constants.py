"""
Page-level content: browser tab titles, static copy, labels and per-page colours.

One section per page (same order as `pages.ROUTES`); names are prefixed with the page so they cannot clash.
Each page module imports what it needs under its local name, e.g.

    from pages.pages_constants import ABOUT_TITLE as TITLE

App-wide settings (tables, env vars, limits, taxonomy) live in the root `constants.py`.
"""

from __future__ import annotations

APP_TITLE = "PIM Policy Profiles AI"

# ─────────────────────────────────────────────────────────────────────────────
# home
# ─────────────────────────────────────────────────────────────────────────────
HOME_TITLE = APP_TITLE
HOME_MODULES = [
    dict(title="Browse curated policies",
         description="View the latest curated PIM policy and strategy documents for any country, in English or the "
                     "original language.",
         page="countries", cta="Browse countries", icon="globe", icon_bg="bg-sky-50", icon_fg="text-sky-600"),
    dict(title="Track policy changes",
         description="See what changed in a country's PIM framework — new, revised and superseded laws, regulations, "
                     "guidance and strategies — since the latest profile update or since a date you choose.",
         page="policy-changes", cta="Check changes", icon="history", icon_bg="bg-teal-50", icon_fg="text-teal-600"),
    dict(title="Contribute or update",
         description="Add new country records, upload original-language and translated source documents, and curate "
                     "metadata.",
         page="record-new", cta="Add a record", icon="upload", icon_bg="bg-emerald-50", icon_fg="text-emerald-600"),
    dict(title="AI Coach analytics",
         description="Ask country-specific questions on appraisal, transparency, PPPs, briefing, and more. Cited "
                     "answers grounded in the curated corpus.",
         page="coach", cta="Open the Coach", icon="sparkles", icon_bg="bg-violet-50", icon_fg="text-violet-600"),
    dict(title="Benchmark to peers",
         description="Compare a country to frontier good-practice and peer jurisdictions across the core PIM "
                     "dimensions.",
         page="benchmark", cta="Compare countries", icon="activity", icon_bg="bg-amber-50", icon_fg="text-amber-600"),
    dict(title="Plug-in APIs",
         description="Documented endpoints for embedding repository data, AI analytics, and benchmarks into national "
                     "ePIM-CBA deployments.",
         page="api-docs", cta="View API docs", icon="plug", icon_bg="bg-rose-50", icon_fg="text-rose-600"),
]

# ─────────────────────────────────────────────────────────────────────────────
# countries
# ─────────────────────────────────────────────────────────────────────────────
COUNTRIES_TITLE = "Countries — PIM Repository"
# IMF PIMA assessment status → wording in the country list
COUNTRIES_PIMA_STATUS_TEXT = {
    "published": "full report published",
    "summary": "high-level summary or IMF reference only (no full report)",
    "unpublished": "conducted, report not published",
    "unknown": "conducted",
}

# ─────────────────────────────────────────────────────────────────────────────
# country  (country_detail)
# ─────────────────────────────────────────────────────────────────────────────
COUNTRY_DETAIL_TITLE = "Policies — PIM Repository"
COUNTRY_DETAIL_LEVEL_ORDER = ["national", "subnational", "supranational"]
COUNTRY_DETAIL_LEVEL_META = {
    "national": dict(label="National", badge="border-slate-200 bg-slate-50 text-slate-600", dot="bg-slate-400"),
    "subnational": dict(label="Sub-national", badge="border-emerald-200 bg-emerald-50 text-emerald-700", dot="bg-emerald-500"),
    "supranational": dict(label="Supranational", badge="border-violet-200 bg-violet-50 text-violet-700", dot="bg-violet-500"),
}

# ─────────────────────────────────────────────────────────────────────────────
# map  (map_page)
# ─────────────────────────────────────────────────────────────────────────────
MAP_TITLE = "Global Map — PIM Policy Profiles AI"
MAP_COLORS = {"english": "#374291", "nonEnglish": "#f26b23"}
MAP_NO_DATA_FILL = "#e8edf2"
MAP_LEGEND = [
    (MAP_COLORS["english"], "Original language English"),
    (MAP_COLORS["nonEnglish"], "Original language not English"),
    (MAP_NO_DATA_FILL, "No policy data"),
]

# ─────────────────────────────────────────────────────────────────────────────
# repository
# ─────────────────────────────────────────────────────────────────────────────
REPOSITORY_TITLE = "Repository — PIM Policy Profiles AI"

# ─────────────────────────────────────────────────────────────────────────────
# batch  (batch_upload)
# ─────────────────────────────────────────────────────────────────────────────
BATCH_UPLOAD_TITLE = "Batch upload — PIM Repository"
BATCH_UPLOAD_STEPS = [("upload", "Upload"), ("preview", "Review"), ("run", "Import")]
# row status → (badge classes, label)
BATCH_UPLOAD_STATUS = {
    "pending": ("bg-blue-50 text-blue-700 border-blue-200", "Ready"),
    "invalid": ("bg-red-50 text-red-700 border-red-200", "Error"),
    "skipped": ("bg-gray-100 text-gray-600 border-gray-200", "Skip (duplicate)"),
    "processing": ("bg-amber-50 text-amber-700 border-amber-200", "Working…"),
    "created": ("bg-green-50 text-green-700 border-green-200", "Created"),
    "updated": ("bg-green-50 text-green-700 border-green-200", "Updated"),
    "failed": ("bg-red-50 text-red-700 border-red-200", "Failed"),
}

# ─────────────────────────────────────────────────────────────────────────────
# record-new / record / record-edit
# ─────────────────────────────────────────────────────────────────────────────
RECORD_NEW_TITLE = "Add New Policy Record — PIM Policy Profiles AI"
RECORD_DETAIL_TITLE = "Record — PIM Repository"
RECORD_EDIT_TITLE = "Edit Record — PIM Policy Profiles AI"

# ─────────────────────────────────────────────────────────────────────────────
# coach / country-profile / country-transparency
# ─────────────────────────────────────────────────────────────────────────────
COACH_TITLE = APP_TITLE
COUNTRY_PROFILE_TITLE = APP_TITLE
COUNTRY_TRANSPARENCY_TITLE = APP_TITLE

# ─────────────────────────────────────────────────────────────────────────────
# raging  (RAG admin console)
# ─────────────────────────────────────────────────────────────────────────────
RAGING_TITLE = "RAG — PIM Policy Profiles AI"
# re-index filter → button label
RAGING_BUTTON_LABELS = {
    "all": "Re-index All Documents",
    "eng": "Re-index English Only",
    "ori": "Add Original Language Docs",
}

# ─────────────────────────────────────────────────────────────────────────────
# benchmark
# ─────────────────────────────────────────────────────────────────────────────
BENCHMARK_TITLE = "Benchmark — PIM Policy Profiles AI"
BENCHMARK_DIMENSIONS = [
    "Strategic planning", "Project appraisal", "Project selection", "Budgeting & financing",
    "Implementation & monitoring", "Asset management", "PPP framework", "Transparency & accountability",
]

# ─────────────────────────────────────────────────────────────────────────────
# statistics
# ─────────────────────────────────────────────────────────────────────────────
STATISTICS_TITLE = "Search Index Analytics — PIM Policy Profiles AI"
# colours (StatisticsContent.tsx) + the shadcn tokens Plotly cannot read from CSS variables
STATISTICS_BLUE = "#1d4ed8"
STATISTICS_BLUE_LIGHT = "#3b82f6"
STATISTICS_TIER_COLORS = ["#1d4ed8", "#2563eb", "#3b82f6", "#60a5fa"]
STATISTICS_LANG_COLORS = ["#1d4ed8", "#10b981"]
STATISTICS_MUTED_FG = "#737373"      # --muted-foreground
STATISTICS_BORDER = "#e5e5e5"        # --border
STATISTICS_FG = "#0a0a0a"            # --foreground
STATISTICS_AXIS = "#666666"          # Recharts' default axis stroke
STATISTICS_FONT = "Inter, ui-sans-serif, system-ui, sans-serif"

# ─────────────────────────────────────────────────────────────────────────────
# policy-changes
# ─────────────────────────────────────────────────────────────────────────────
POLICY_CHANGES_TITLE = "PIM Policy Changes — PIM Policy Profiles AI"
POLICY_CHANGES_KIND_STYLE = {
    "new_instrument": "border-blue-200 bg-blue-50 text-blue-700",
    "revised_instrument": "border-violet-200 bg-violet-50 text-violet-700",
    "superseded": "border-amber-200 bg-amber-50 text-amber-700",
    "added_to_profile": "border-emerald-200 bg-emerald-50 text-emerald-700",
    "updated_in_profile": "border-slate-200 bg-slate-50 text-slate-600",
}
POLICY_CHANGES_TIER_DESC = {1: "Primary legislation", 2: "Secondary regulations",
                            3: "Procedural & methodological guidance", 4: "Strategies & plans"}

# ─────────────────────────────────────────────────────────────────────────────
# download / api-docs
# ─────────────────────────────────────────────────────────────────────────────
DOWNLOAD_TITLE = "Download — PIM Repository"
API_DOCS_TITLE = "API — PIM Policy Profiles AI"

# ─────────────────────────────────────────────────────────────────────────────
# about
# ─────────────────────────────────────────────────────────────────────────────
ABOUT_TITLE = "About · PIM Policy Profiles AI"
ABOUT_POLICY_TIERS = [
    dict(code="Tier 1", label="Primary Legislation", tagline='the "why" and "what"',
         badge="bg-blue-50 text-blue-700 border-blue-200", border="border-l-blue-400",
         body="The foundational legal authority for PIM, typically established through an Organic Budget Law, Public "
              "Finance Management Law, or a dedicated Public Investment Law. This tier sets the overall objectives "
              "and principles of public investment, establishes which entities are subject to the rules, and "
              "defines key concepts. It should be durable and not change frequently. In most countries, this is "
              "where the mandate for PIM is anchored — without this, the framework lacks legal force."),
    dict(code="Tier 2", label="Secondary Regulations / Decrees", tagline='the "who"',
         badge="bg-indigo-50 text-indigo-700 border-indigo-200", border="border-l-indigo-400",
         body="Regulations, decrees, or statutory instruments issued under the authority of Tier 1. They specify "
              "roles and responsibilities of different institutions (e.g., the Ministry of Finance, sector "
              "ministries, line agencies), set the rules of the process in more detail, and establish "
              "accountability and compliance requirements. They are more specific than primary legislation but "
              "still relatively high-level."),
    dict(code="Tier 3", label="Procedural Guidelines and Methodological Guidance", tagline='the "how"',
         badge="bg-violet-50 text-violet-700 border-violet-200", border="border-l-violet-400",
         body="Operational manuals, circulars, and technical guidelines that provide the practical step-by-step "
              "instructions for how to carry out appraisal, selection, implementation and evaluation of projects. "
              "This is where, for example, cost-benefit analysis methodology and project appraisal templates live. "
              "These can and should be updated relatively frequently as practice evolves."),
    dict(code="Tier 4", label="Strategies for Project Prioritization & Alignment", tagline=None,
         badge="bg-teal-50 text-teal-700 border-teal-200", border="border-l-teal-400", body=None),
]
ABOUT_STRATEGY_TYPES = [
    (1, "National (Longer Term, 5 Years+)"), (2, "Medium Term (3–5 years)"), (3, "Sectoral"),
    (4, "Cross-Cutting (Climate)"), (5, "Cross-Cutting (Other)"), (6, "Sub-National Strategy"),
]
ABOUT_REFERENCES = [
    dict(citation="Kim, J.-H., Fallov, J. A., & Groom, S. (2020). Public Investment Management Reference Guide. "
                  "Washington, DC: World Bank International Development in Practice, pp. 279.",
         url="https://openknowledge.worldbank.org/server/api/core/bitstreams/2e0fa0e5-254e-5119-ab70-4ebfebc3f8ef/content",
         link_label="Open Knowledge Repository"),
    dict(citation="Manescu, C. B. (2024). The Planning of Public Investments in EU Member States: Long-Term Strategy, "
                  "Selection & Budgeting Issues. Brussels: European Commission Directorate-General for Economic and "
                  "Financial Affairs, European Economy Discussion Papers, 213, December, pp. 36.",
         url="https://economy-finance.ec.europa.eu/publications/planning-public-investments-eu-member-states-long-term-strategy-selection-and-budgeting-issues_en",
         link_label="European Commission Publications"),
]

# ─────────────────────────────────────────────────────────────────────────────
# release-notes
# ─────────────────────────────────────────────────────────────────────────────
RELEASE_NOTES_TITLE = "Release notes · PIM Policy Profiles AI"
RELEASE_NOTES_CHANGE_TYPE_STYLES = {
    "feature": dict(label="New", cls="bg-blue-50 text-blue-700 border-blue-200", dot="bg-blue-500"),
    "improvement": dict(label="Improved", cls="bg-violet-50 text-violet-700 border-violet-200", dot="bg-violet-500"),
    "fix": dict(label="Fixed", cls="bg-emerald-50 text-emerald-700 border-emerald-200", dot="bg-emerald-500"),
    "breaking": dict(label="Breaking", cls="bg-red-50 text-red-700 border-red-200", dot="bg-red-500"),
}

# ─────────────────────────────────────────────────────────────────────────────
# signin / register / not found / error
# ─────────────────────────────────────────────────────────────────────────────
SIGNIN_TITLE = "Sign in — PIM Policy Profiles AI"
REGISTER_TITLE = "Create an account — PIM Policy Profiles AI"
NOT_FOUND_TITLE = APP_TITLE
ERROR_PAGE_TITLE = APP_TITLE
