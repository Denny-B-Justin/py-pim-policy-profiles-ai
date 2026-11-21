"""
Central constants for the PIM Policy Profiles AI Dash app.

Everything that is configurable lives here or in the environment (.env).
See .env.sample for the full list of variables.  Page copy (titles, card text,
legends, per-page colours) lives in pages/pages_constants.py.

Sections
--------
1. Branding / app metadata, web server + session
2. Databricks (Azure) Unity Catalog connection  ->  prd_mega.sgpbpi163
3. Unity Catalog table + volume names, local paths
4. mAI gateway (only TWO environment variables are required)
5. Taxonomy (policy tiers, strategy types, jurisdictions, ...)
6. Limits / tuning knobs
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
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

# Web server (only used by `python app.py`; Posit Connect runs app:app itself)
LOG_LEVEL = _env("LOG_LEVEL", "INFO")
DASH_DEBUG = _env("DASH_DEBUG", "0") == "1"
HOST = "0.0.0.0"
PORT = _env_int("PORT", 8050)

# Local sign-in.  false (default) = no sign-in / register pages (they answer 404) and every visitor may make
# changes as "Guest" – restrict who can open the app with the Posit Connect content access list.  Posit Connect
# identities are still recognised and recorded in the audit log.
AUTH_ENABLED = _env("AUTH_ENABLED", "false").lower() in ("1", "true", "yes")

# Signed session cookie for local accounts
SESSION_COOKIE_NAME = "pim_session"
SESSION_COOKIE_SAMESITE = "Lax"
SESSION_LIFETIME_DAYS = 30
# Secure = the browser sends the cookie over HTTPS only (Posit Connect; browsers also allow it on http://localhost)
SESSION_COOKIE_SECURE = _env("PIM_SESSION_COOKIE_SECURE", "1") in ("1", "true", "True")
SESSION_RECHECK_SECONDS = 60                  # how long a session's user row is trusted before it is re-read
BCRYPT_ROUNDS = 12                            # password hash cost (older, cheaper hashes are upgraded at sign-in)

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
# Table names (the prefix keeps them easy to find in a shared schema).
TABLE_PREFIX = "policy_repo_"
TABLES: dict[str, str] = {
    "countries": f"{TABLE_PREFIX}countries",                        # was  countries
    "policy_records": f"{TABLE_PREFIX}policy_records",              # was  policy_records
    "documents": f"{TABLE_PREFIX}documents",                        # was  documents
    "users": f"{TABLE_PREFIX}users",                                # was  users
    "audit_logs": f"{TABLE_PREFIX}audit_logs",                      # was  audit_logs
    "partner_api_keys": f"{TABLE_PREFIX}partner_api_keys",          # was  partner_api_keys (not created yet)
    "batch_imports": f"{TABLE_PREFIX}batch_imports",                # was  batch_imports
    "batch_import_rows": f"{TABLE_PREFIX}batch_import_rows",        # was  batch_import_rows (jsonb -> STRING)
    "briefings_cache": f"{TABLE_PREFIX}briefings_cache",            # was  briefings_cache
    "imf_pima_assessments": f"{TABLE_PREFIX}imf_pima_assessments",  # was  imf_pima_assessments
    # Search index (replaces langchain_pg_embedding / chunk_parents and the old JSON chunk files):
    "document_chunks": f"{TABLE_PREFIX}document_chunks",            # one row per chunk – source of the AI Search index
    "document_ingest_log": f"{TABLE_PREFIX}document_ingest_log",    # one row per document – last chunking outcome
}

# Partner API (/api/v1/*) needs the partner_api_keys table, which does not exist yet → off by default.
PARTNER_API_ENABLED = _env("PIM_PARTNER_API", "0") in ("1", "true", "True")

# Unity Catalog Volume folder the app uploads documents into (write-only: the app never reads files back):
#   <FILES_ROOT>/documents/upload-documents/<ISO3>_<Country>/<National|Subnational>/<id8>_<file name>
UC_VOLUME = _env("DATABRICKS_VOLUME", "pimpam_policy")
UC_VOLUME_ROOT = f"/Volumes/{DATABRICKS_CATALOG}/{DATABRICKS_SCHEMA}/{UC_VOLUME}"
FILES_FOLDER = _env("PIM_FILES_FOLDER", "PIMPAM-AI/policy-profiles/upload-files").strip("/")
FILES_ROOT = f"{UC_VOLUME_ROOT}/{FILES_FOLDER}"
DOCUMENTS_DIR = _env("PIM_DOCUMENTS_FOLDER", "documents/upload-documents").strip("/")
# Local folder used instead of the volume when PIM_DB_BACKEND=sqlite (same documents/ layout)
LOCAL_STORAGE_DIR = Path(_env("PIM_LOCAL_STORAGE", str(BASE_DIR / ".local" / "files")))

# Bundled files
DATA_DIR = BASE_DIR / "data"
FONTS_DIR = BASE_DIR / "fonts"
COUNTRIES_JSON = DATA_DIR / "countries.json"
IMF_PIMA_REFERENCES_JSON = DATA_DIR / "imf-pima-references.json"
OPENAPI_JSON = DATA_DIR / "openapi.json"
RELEASE_NOTES_JSON = DATA_DIR / "release_notes.json"
# Batch-upload template + data dictionary (paths relative to assets/)
BATCH_TEMPLATE_ASSET = "templates/PIM_Country_Policy_Profile_Batch_Upload_Template.xlsx"
BATCH_DATADICT_ASSET = "templates/PIM_Batch_Upload_Data_Dictionary.docx"

# Background job state (JSON files shared by all worker processes on the host)
JOBS_DIR = Path(_env("PIM_JOBS_DIR") or Path(tempfile.gettempdir()) / "pim_jobs")

# ─────────────────────────────────────────────────────────────────────────────
# 4. mAI gateway  (Posit mAI Gateway – Developer Guide)
# ─────────────────────────────────────────────────────────────────────────────
# Set in Posit Connect -> Settings -> Runtime -> Environment Variables (see .env.sample):
#   MAI_BASE_URL          gateway URL, MUST end with "/"  (QA and Production differ)
#   MAI_ENDPOINT_OPENAI   mai-endpoint for GPT     (…/maifactory/openai/responses)
#   MAI_ENDPOINT_CLAUDE   mai-endpoint for Claude  (…/maifactory/bedrock/model/us.anthropic.<model>/converse)
#   MAI_ENDPOINT_GEMINI   mai-endpoint for Gemini  (…/maifactory/gemini/interactions)
# Set the endpoints of the providers you want; models of a provider without an
# endpoint are not offered.  A single MAI_ENDPOINT (the guide's one-provider setup)
# also works – its provider is detected from the URL.
# Nothing else: no API keys, tokens or client details.  The app id header
# (X-Posit-App-Id) is taken from CONNECT_CONTENT_GUID which Posit Connect sets
# automatically for every deployed app.
MAI_BASE_URL = _env("MAI_BASE_URL")
MAI_ENDPOINTS: dict[str, str] = {
    "openai": _env("MAI_ENDPOINT_OPENAI"),
    "claude": _env("MAI_ENDPOINT_CLAUDE"),
    "gemini": _env("MAI_ENDPOINT_GEMINI"),
}
MAI_ENDPOINT = _env("MAI_ENDPOINT")             # optional single-provider alternative to the three above
# Optional default model (must be in MAI_MODELS and its provider configured);
# empty = the first configured model below.
MAI_MODEL = _env("MAI_MODEL")

# Models the app offers: the two most capable gateway-approved models per provider
# (Claude has only one).  Order = preference for the default model.  Other approved
# models (gemini-3.1/3.5-flash-lite, gemini-3.6-flash) are deliberately not offered.
MAI_MODELS: list[dict[str, str]] = [
    {"id": "gpt-6-luna", "provider": "openai", "label": "GPT-6 Luna"},
    {"id": "gpt-5.6-luna", "provider": "openai", "label": "GPT-5.6 Luna"},
    {"id": "claude-haiku-5-5", "provider": "claude", "label": "Claude Haiku 5.5"},
    {"id": "gemini-3.8-flash", "provider": "gemini", "label": "Gemini 3.8 Flash"},
    {"id": "gemini-3.7-flash", "provider": "gemini", "label": "Gemini 3.7 Flash"},
]
MAI_PROVIDER_LABELS = {"openai": "OpenAI", "claude": "Anthropic", "gemini": "Google"}
MAI_SOURCE_TYPE = "interactive"
MAI_TIMEOUT_SECONDS = _env_int("MAI_TIMEOUT_SECONDS", 240)
# Local UI preview without gateway access (the gateway rejects non-Connect hosts).
MAI_MOCK = _env("PIM_MAI_MOCK", "0") in ("1", "true", "True")

# Generation budgets
CHAT_MAX_TOKENS = 2048
BRIEFING_MAX_TOKENS = 8192
# Cap on the visible text of long-form briefings (original: 12288 incl. reasoning for the profile).
PROFILE_MAX_TOKENS = 12288
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

# Batch import: fetching documents from the spreadsheet's URLs
BATCH_FETCH_TIMEOUT = 30                      # seconds
BATCH_MAX_REDIRECTS = 5
BATCH_MAX_DOC_BYTES = 25 * 1024 * 1024
BATCH_STUCK_ROW_MINUTES = 5                   # a row "processing" for longer than this is assumed abandoned

# Source-link checker
LINK_CHECK_TIMEOUT = 12                       # seconds
LINK_CHECK_MAX_REDIRECTS = 5
LINK_CHECK_USER_AGENT = "PIM-LinkCheck/1.0"

# Background jobs
JOB_WORKERS = _env_int("PIM_JOB_WORKERS", 6)
JOB_TTL_SECONDS = 6 * 3600

# Translation
MAX_TRANSLATE_CHARS = _env_int("PIM_MAX_TRANSLATE_CHARS", 600000)
TRANSLATE_WORKERS = _env_int("PIM_TRANSLATE_WORKERS", 3)

# Text extraction / text-layer detection
EXTRACT_SAMPLE_PAGES = 6
EXTRACT_MIN_CHARS_PER_PAGE = 40
EXTRACT_MAX_DETECT_BYTES = 40 * 1024 * 1024

# Search index + retrieval
# Chunk sizes live in utils/chunking.py (); these two are
# informational copies for the statistics pages.
CHUNK_SIZE = 1000

# Retrieval: Databricks AI Search (formerly Vector Search) Delta Sync index over policy_repo_document_chunks –
# hybrid (vector + keyword) search, embeddings computed by Databricks.  "ai_search" is the only backend; any other
# value switches document search off.  Not available in the local SQLite preview.
SEARCH_BACKEND = _env("PIM_SEARCH_BACKEND", "ai_search").lower()
AI_SEARCH_ENDPOINT = _env("PIM_AI_SEARCH_ENDPOINT", "pim_policy_profiles_ai_search")
AI_SEARCH_INDEX = _env("PIM_AI_SEARCH_INDEX",
                       f"{DATABRICKS_CATALOG}.{DATABRICKS_SCHEMA}.{TABLES['document_chunks']}_index")
AI_SEARCH_EMBEDDING_MODEL = _env("PIM_AI_SEARCH_EMBEDDING_MODEL", "databricks-qwen3-embedding-0-6b")  # informational
AI_SEARCH_QUERY_TYPE = _env("PIM_AI_SEARCH_QUERY_TYPE", "HYBRID").upper()       # HYBRID | ANN
AI_SEARCH_RERANK = _env("PIM_AI_SEARCH_RERANK", "0") in ("1", "true", "True")   # Databricks reranker (extra latency)
AI_SEARCH_SYNC_ON_WRITE = _env("PIM_AI_SEARCH_SYNC", "1") in ("1", "true", "True")  # trigger a sync after uploads
AI_SEARCH_SYNC_DEBOUNCE_SECONDS = _env_int("PIM_AI_SEARCH_SYNC_DEBOUNCE", 20)
CHUNK_WRITE_BATCH = 100                       # chunk rows per MERGE statement (one JSON parameter per batch)
RETRIEVER_K = 6
BRIEFING_RETRIEVER_K = 20
MAX_CONTEXT_TOKENS = 6000

# Briefings (stored / exported markdown)
BRIEFING_MIN_CONTENT = 100
BRIEFING_MAX_CONTENT = 500_000

# Partner API keys:  pim_v1_<32 hex chars>
API_KEY_PREFIX = "pim_v1_"
API_KEY_RANDOM_HEX_CHARS = 32                 # 128 bits of entropy

SECRET_KEY = _env("PIM_SECRET_KEY", "") or _env("FLASK_SECRET_KEY", "")
EXPORT_ALLOWED_ORIGINS = _env("EXPORT_ALLOWED_ORIGINS")   # comma-separated CORS origins for GET /api/export/documents (default: any)
CRON_SECRET = _env("CRON_SECRET")               # optional bearer token for GET /api/imf-pima/refresh
ADMIN_INVITE_CODE = _env("ADMIN_INVITE_CODE")   # required for registration after the first account (same name as the Next.js app)
IMF_PIMA_BASE = "https://infrastructuregovern.imf.org"
IMF_PIMA_LIST_URL = f"{IMF_PIMA_BASE}/content/PIMA/Home/Region-and-Country-Information.html"
IMF_PIMA_ABOUT_URL = f"{IMF_PIMA_BASE}/content/PIMA/Home/PimaTool/What-is-PIMA.html"
