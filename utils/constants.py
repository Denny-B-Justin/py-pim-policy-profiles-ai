"""
Central constants for the PIM Policy Profiles AI Dash app.

Everything that is configurable lives here or in the environment (.env).
See .env.sample for the full list of variables.

Sections
--------
1. Branding / app metadata
2. Databricks (Azure) Unity Catalog connection  ->  prd_mega.sgpbpi163
3. Unity Catalog table + volume names (what to call the tables when migrating)
4. mAI gateway (only TWO environment variables are required)
5. Taxonomy (policy tiers, strategy types, jurisdictions, ...)
6. Limits / tuning knobs
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _env_int(name: str, default: int) -> int:
    try:
        return int(_env(name, str(default)))
    except ValueError:
        return default


# ─────────────────────────────────────────────────────────────────────────────
# 1. Branding
# ─────────────────────────────────────────────────────────────────────────────
APP_NAME = "PIM Policy Profiles AI"
APP_DESCRIPTION = (
    "Curated Public Investment Management policy intelligence — country repository, "
    "AI Coach analytics, and benchmarking."
)
FOOTER_TEXT = "PIM Policy Profiles AI · Public Investment Management"
# Public origin used inside generated briefings ("contribute at <url>/").
# Leave empty to fall back to the URL of the running app.
PUBLIC_BASE_URL = _env("PUBLIC_BASE_URL")

# Default page (served at "/" and at "/?page=home")
DEFAULT_PAGE = "home"

# ─────────────────────────────────────────────────────────────────────────────
# 2. Databricks / Azure Unity Catalog
# ─────────────────────────────────────────────────────────────────────────────
# Catalog + schema are fixed for this deployment (prd_mega.sgpbpi163).
DATABRICKS_CATALOG = _env("DATABRICKS_CATALOG", "prd_mega")
DATABRICKS_SCHEMA = _env("DATABRICKS_SCHEMA", "sgpbpi163")

# Workspace host, e.g. adb-1234567890123456.7.azuredatabricks.net  (no https://)
DATABRICKS_SERVER_HOSTNAME = _env("DATABRICKS_SERVER_HOSTNAME").replace("https://", "").rstrip("/")
# SQL warehouse HTTP path, e.g. /sql/1.0/warehouses/abcdef0123456789
DATABRICKS_HTTP_PATH = _env("DATABRICKS_HTTP_PATH")
# OAuth machine-to-machine credentials of the service principal that owns access
# to prd_mega.sgpbpi163.  NEVER commit real values — set them in Posit Connect
# (Settings -> Vars) or in a local .env file.
DATABRICKS_CLIENT_ID = _env("DATABRICKS_CLIENT_ID")
DATABRICKS_CLIENT_SECRET = _env("DATABRICKS_CLIENT_SECRET")

# "databricks" (production) | "sqlite" (local UI preview only, see README)
DB_BACKEND = _env("PIM_DB_BACKEND", "databricks").lower()
SQLITE_PATH = _env("PIM_SQLITE_PATH", str(BASE_DIR / ".local" / "demo.sqlite"))

# ─────────────────────────────────────────────────────────────────────────────
# 3. Unity Catalog objects
# ─────────────────────────────────────────────────────────────────────────────
# Ideal table names (prefix `pim_` keeps them easy to find in a shared schema).
# These are the names to use when migrating the Neon/Postgres tables — the
# mapping from the original Postgres table is in docs/DATABRICKS_MIGRATION.md.
TABLE_PREFIX = "pim_"
TABLES: dict[str, str] = {
    "countries": "pim_countries",                       # was  countries
    "policy_records": "pim_policy_records",             # was  policy_records
    "documents": "pim_documents",                       # was  documents
    "users": "pim_users",                               # was  users
    "audit_logs": "pim_audit_logs",                     # was  audit_logs
    "partner_api_keys": "pim_partner_api_keys",         # was  partner_api_keys
    "batch_imports": "pim_batch_imports",               # was  batch_imports
    "batch_import_rows": "pim_batch_import_rows",       # was  batch_import_rows (jsonb -> STRING)
    "briefings_cache": "pim_briefings_cache",           # was  briefings_cache
    "imf_pima_assessments": "pim_imf_pima_assessments", # was  imf_pima_assessments
    "document_chunks": "pim_document_chunks",           # was  langchain_pg_embedding + chunk_parents (pgvector)
}

# Unity Catalog Volume that replaces Vercel Blob (policy PDFs / DOCX files).
UC_VOLUME = _env("DATABRICKS_VOLUME", "pim_documents")
UC_VOLUME_ROOT = f"/Volumes/{DATABRICKS_CATALOG}/{DATABRICKS_SCHEMA}/{UC_VOLUME}"
# Local folder used instead of the volume when PIM_DB_BACKEND=sqlite
LOCAL_STORAGE_DIR = Path(_env("PIM_LOCAL_STORAGE", str(BASE_DIR / ".local" / "files")))

# ─────────────────────────────────────────────────────────────────────────────
# 4. mAI gateway  (Posit mAI Gateway – Developer Guide)
# ─────────────────────────────────────────────────────────────────────────────
# Only TWO variables are required (set them in Posit Connect -> Settings -> Vars):
#   MAI_BASE_URL  gateway URL, MUST end with "/"
#   MAI_ENDPOINT  the mai-endpoint for your provider (OpenAI / Claude / Gemini)
# Nothing else: no API keys, tokens or client details.  The app id header
# (X-Posit-App-Id) is taken from CONNECT_CONTENT_GUID which Posit Connect sets
# automatically for every deployed app.
MAI_BASE_URL = _env("MAI_BASE_URL")
MAI_ENDPOINT = _env("MAI_ENDPOINT")
# Optional.  Only needed for OpenAI / Gemini endpoints (Claude's model is part of
# the endpoint URL).  Must be one of MAI_ALLOWED_MODELS.
MAI_MODEL = _env("MAI_MODEL")

MAI_ALLOWED_MODELS: dict[str, list[str]] = {
    "claude": ["claude-haiku-5-5"],
    "openai": ["gpt-6-luna", "gpt-5.6-luna"],
    "gemini": [
        "gemini-3.1-flash-lite",
        "gemini-3.5-flash-lite",
        "gemini-3.6-flash",
        "gemini-3.7-flash",
        "gemini-3.8-flash",
    ],
}
MAI_DEFAULT_MODEL = {
    "claude": "claude-haiku-5-5",
    "openai": "gpt-6-luna",
    "gemini": "gemini-3.8-flash",
}
MAI_SOURCE_TYPE = "interactive"
MAI_TIMEOUT_SECONDS = _env_int("MAI_TIMEOUT_SECONDS", 240)
# Local UI preview without gateway access (the gateway rejects non-Connect hosts).
MAI_MOCK = _env("PIM_MAI_MOCK", "0") in ("1", "true", "True")

# Generation budgets
CHAT_MAX_TOKENS = 2048
BRIEFING_MAX_TOKENS = 8192
TRANSLATE_CHUNK_CHARS = 6000

# ─────────────────────────────────────────────────────────────────────────────
# 5. Taxonomy
# ─────────────────────────────────────────────────────────────────────────────
TIERS = [
    {"value": 1, "label": "Tier 1 — Primary Legislation"},
    {"value": 2, "label": "Tier 2 — Secondary Regulations"},
    {"value": 3, "label": "Tier 3 — Procedural Guidelines and Methodological Guidance"},
    {"value": 4, "label": "Tier 4 — Strategies for Project Prioritization & Alignment"},
]
STRATEGY_TYPES = [
    {"value": 1, "label": "National (Longer Term, 5 Years+)", "short": "NAT"},
    {"value": 2, "label": "Medium Term (3-5 Years)", "short": "MED"},
    {"value": 3, "label": "Sectoral", "short": "SEC"},
    {"value": 4, "label": "Cross-Cutting (Climate)", "short": "CC-CL"},
    {"value": 5, "label": "Cross-Cutting (Other)", "short": "CC-OT"},
    {"value": 6, "label": "Sub-National Strategy", "short": "SUB"},
]
JURISDICTION_LEVELS = ["national", "subnational", "supranational"]
RECORD_STATUSES = ["latest", "archived"]

TRANSLATION_SOURCES = [
    {"value": "official", "label": "Official"},
    {"value": "mai", "label": "mAI Translate"},
    {"value": "deepl", "label": "DeepL"},
    {"value": "google", "label": "Google Translate"},
    {"value": "wbg", "label": "WBG Translate"},
    {"value": "other", "label": "Other"},
]
TRANSLATION_SOURCE_BADGE = {
    "official": "bg-emerald-50 text-emerald-700 border-emerald-200",
    "mai": "bg-violet-50 text-violet-700 border-violet-200",
    "deepl": "bg-violet-50 text-violet-700 border-violet-200",
    "google": "bg-sky-50 text-sky-700 border-sky-200",
    "wbg": "bg-blue-50 text-blue-700 border-blue-200",
    "other": "bg-gray-100 text-gray-600 border-gray-200",
}

# Languages offered for an original-language document (all Google-Translate languages + "other").
NATIVE_LANGUAGES = [
    ("af", "Afrikaans"),
    ("sq", "Albanian"),
    ("am", "Amharic"),
    ("ar", "Arabic"),
    ("hy", "Armenian"),
    ("as", "Assamese"),
    ("ay", "Aymara"),
    ("az", "Azerbaijani"),
    ("bm", "Bambara"),
    ("eu", "Basque"),
    ("be", "Belarusian"),
    ("bn", "Bengali"),
    ("bho", "Bhojpuri"),
    ("bs", "Bosnian"),
    ("bg", "Bulgarian"),
    ("ca", "Catalan"),
    ("ceb", "Cebuano"),
    ("zh-CN", "Chinese (Simplified)"),
    ("zh-TW", "Chinese (Traditional)"),
    ("co", "Corsican"),
    ("hr", "Croatian"),
    ("cs", "Czech"),
    ("da", "Danish"),
    ("dv", "Dhivehi"),
    ("doi", "Dogri"),
    ("nl", "Dutch"),
    ("en", "English"),
    ("eo", "Esperanto"),
    ("et", "Estonian"),
    ("ee", "Ewe"),
    ("fil", "Filipino (Tagalog)"),
    ("fi", "Finnish"),
    ("fr", "French"),
    ("fy", "Frisian"),
    ("gl", "Galician"),
    ("ka", "Georgian"),
    ("de", "German"),
    ("el", "Greek"),
    ("gn", "Guarani"),
    ("gu", "Gujarati"),
    ("ht", "Haitian Creole"),
    ("ha", "Hausa"),
    ("haw", "Hawaiian"),
    ("iw", "Hebrew"),
    ("hi", "Hindi"),
    ("hmn", "Hmong"),
    ("hu", "Hungarian"),
    ("is", "Icelandic"),
    ("ig", "Igbo"),
    ("ilo", "Ilocano"),
    ("id", "Indonesian"),
    ("ga", "Irish"),
    ("it", "Italian"),
    ("ja", "Japanese"),
    ("jv", "Javanese"),
    ("kn", "Kannada"),
    ("kk", "Kazakh"),
    ("km", "Khmer"),
    ("rw", "Kinyarwanda"),
    ("gom", "Konkani"),
    ("ko", "Korean"),
    ("kri", "Krio"),
    ("ku", "Kurdish (Kurmanji)"),
    ("ckb", "Kurdish (Sorani)"),
    ("ky", "Kyrgyz"),
    ("lo", "Lao"),
    ("la", "Latin"),
    ("lv", "Latvian"),
    ("ln", "Lingala"),
    ("lt", "Lithuanian"),
    ("lg", "Luganda"),
    ("lb", "Luxembourgish"),
    ("mk", "Macedonian"),
    ("mai", "Maithili"),
    ("mg", "Malagasy"),
    ("ms", "Malay"),
    ("ml", "Malayalam"),
    ("mt", "Maltese"),
    ("mi", "Maori"),
    ("mr", "Marathi"),
    ("mni-Mtei", "Meitei (Manipuri)"),
    ("lus", "Mizo"),
    ("mn", "Mongolian"),
    ("my", "Myanmar (Burmese)"),
    ("ne", "Nepali"),
    ("no", "Norwegian"),
    ("ny", "Nyanja (Chichewa)"),
    ("or", "Odia (Oriya)"),
    ("om", "Oromo"),
    ("ps", "Pashto"),
    ("fa", "Persian"),
    ("pl", "Polish"),
    ("pt", "Portuguese"),
    ("pa", "Punjabi"),
    ("qu", "Quechua"),
    ("ro", "Romanian"),
    ("ru", "Russian"),
    ("sm", "Samoan"),
    ("sa", "Sanskrit"),
    ("gd", "Scots Gaelic"),
    ("nso", "Sepedi"),
    ("sr", "Serbian"),
    ("st", "Sesotho"),
    ("sn", "Shona"),
    ("sd", "Sindhi"),
    ("si", "Sinhala (Sinhalese)"),
    ("sk", "Slovak"),
    ("sl", "Slovenian"),
    ("so", "Somali"),
    ("es", "Spanish"),
    ("su", "Sundanese"),
    ("sw", "Swahili"),
    ("sv", "Swedish"),
    ("tg", "Tajik"),
    ("ta", "Tamil"),
    ("tt", "Tatar"),
    ("te", "Telugu"),
    ("th", "Thai"),
    ("ti", "Tigrinya"),
    ("ts", "Tsonga"),
    ("tr", "Turkish"),
    ("tk", "Turkmen"),
    ("ak", "Twi (Akan)"),
    ("uk", "Ukrainian"),
    ("ur", "Urdu"),
    ("ug", "Uyghur"),
    ("uz", "Uzbek"),
    ("vi", "Vietnamese"),
    ("cy", "Welsh"),
    ("xh", "Xhosa"),
    ("yi", "Yiddish"),
    ("yo", "Yoruba"),
    ("zu", "Zulu"),
    ("other", "Other"),
]

# ─────────────────────────────────────────────────────────────────────────────
# 6. Limits / tuning
# ─────────────────────────────────────────────────────────────────────────────
MAX_UPLOAD_BYTES = 250 * 1024 * 1024          # per attached document
MAX_BATCH_SHEET_BYTES = 5 * 1024 * 1024       # batch upload spreadsheet
ALLOWED_DOC_EXTENSIONS = (".pdf", ".doc", ".docx", ".txt")
PAGE_SIZE = 20                                # repository table page size
QUERY_CACHE_SECONDS = _env_int("PIM_CACHE_SECONDS", 60)
RETRIEVER_K = 6
BRIEFING_RETRIEVER_K = 20
CHUNK_PARENT_CHARS = 2000
MAX_CONTEXT_TOKENS = 6000
SECRET_KEY = _env("PIM_SECRET_KEY", "") or _env("FLASK_SECRET_KEY", "")
EXPORT_ALLOWED_ORIGINS = _env("EXPORT_ALLOWED_ORIGINS")   # comma-separated CORS origins for GET /api/export/documents (default: any)
CRON_SECRET = _env("CRON_SECRET")               # optional bearer token for GET /api/imf-pima/refresh and /api/rag-sync/reconcile
RAG_ADMIN_KEY = _env("RAG_ADMIN_KEY")           # optional extra key for ?page=raging (the original's “Admin API Key” field); unset = sign-in only
ADMIN_INVITE_CODE = _env("ADMIN_INVITE_CODE")   # required for registration after the first account (same name as the Next.js app)
IMF_PIMA_BASE = "https://infrastructuregovern.imf.org"
IMF_PIMA_LIST_URL = f"{IMF_PIMA_BASE}/content/PIMA/Home/Region-and-Country-Information.html"
IMF_PIMA_ABOUT_URL = f"{IMF_PIMA_BASE}/content/PIMA/Home/PimaTool/What-is-PIMA.html"
