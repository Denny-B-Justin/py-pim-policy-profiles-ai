-- ============================================================================================================
-- PIM-PAM policy repository  ·  Unity Catalog objects for prd_mega.sgpbpi163
--
-- Generated from utils/schema.py   (`python scripts/create_tables.py --print` prints the same DDL)
-- Run in the Databricks SQL editor, or execute `python scripts/create_tables.py`.
--
-- Table naming:  policy_repo_<name of the old Postgres table>     e.g.  policy_records  ->  policy_repo_policy_records
-- Constraints:   PRIMARY KEY / FOREIGN KEY are informational in Unity Catalog (not enforced) – the app checks
--                uniqueness and referential integrity itself.
-- Booleans:      machine_readable / text_layer are BOOLEAN;  partner_api_keys.active and link_ok are SMALLINT (0/1) as before.
-- JSON:          batch_import_rows.raw / errors hold JSON text (the old jsonb columns).
-- ============================================================================================================

-- Permissions the app's service principal needs (replace <client-id> with DATABRICKS_CLIENT_ID):
--   GRANT USE CATALOG ON CATALOG prd_mega TO `<client-id>`;
--   GRANT USE SCHEMA, SELECT, MODIFY, CREATE TABLE, CREATE VOLUME ON SCHEMA prd_mega.sgpbpi163 TO `<client-id>`;
--   GRANT READ VOLUME, WRITE VOLUME ON VOLUME prd_mega.sgpbpi163.policy_repo_documents TO `<client-id>`;   -- after the volume exists

-- policy_repo_countries  ←  Postgres `countries`
CREATE TABLE IF NOT EXISTS prd_mega.sgpbpi163.policy_repo_countries (
  iso3 STRING NOT NULL,
  name STRING NOT NULL,
  gdp_2024_bn DECIMAL(12,3),
  pub_inv_pct_gdp DECIMAL(6,4),
  pub_inv_2024_bn DECIMAL(12,3),
  CONSTRAINT pk_policy_repo_countries PRIMARY KEY (iso3)
)
USING DELTA
COMMENT 'ISO3-keyed country lookup with 2024 GDP / public-investment estimates (IMF WEO Oct 2024).';

-- policy_repo_policy_records  ←  Postgres `policy_records`
CREATE TABLE IF NOT EXISTS prd_mega.sgpbpi163.policy_repo_policy_records (
  id STRING NOT NULL,
  country STRING NOT NULL,
  name_eng STRING NOT NULL,
  name_orig STRING,
  year SMALLINT,
  source STRING,
  year_revised SMALLINT,
  overview STRING,
  policy_guidance_tier SMALLINT,
  strategy_tier SMALLINT,
  jurisdiction_level STRING NOT NULL,
  jurisdiction_name STRING,
  record_status STRING NOT NULL,
  superseded_by_link STRING,
  superseded_by_note STRING,
  machine_readable BOOLEAN,
  comment STRING,
  link STRING,
  pages SMALLINT,
  tokens INT,
  link_ok SMALLINT,
  link_checked_at TIMESTAMP,
  link_last_ok_at TIMESTAMP,
  created_at TIMESTAMP NOT NULL,
  updated_at TIMESTAMP NOT NULL,
  CONSTRAINT pk_policy_repo_policy_records PRIMARY KEY (id),
  CONSTRAINT fk_policy_repo_policy_records_country FOREIGN KEY (country) REFERENCES prd_mega.sgpbpi163.policy_repo_countries(iso3)
)
USING DELTA
COMMENT 'One row per curated PIM policy / strategy document (metadata only).';

-- policy_repo_documents  ←  Postgres `documents`
CREATE TABLE IF NOT EXISTS prd_mega.sgpbpi163.policy_repo_documents (
  id STRING NOT NULL,
  record_id STRING NOT NULL,
  lang_type STRING NOT NULL,
  lang_code STRING,
  lang_label STRING,
  translation_source STRING,
  text_layer BOOLEAN,
  file_path STRING NOT NULL,
  file_name STRING,
  file_size INT,
  uploaded_at TIMESTAMP NOT NULL,
  CONSTRAINT pk_policy_repo_documents PRIMARY KEY (id),
  CONSTRAINT fk_policy_repo_documents_record_id FOREIGN KEY (record_id) REFERENCES prd_mega.sgpbpi163.policy_repo_policy_records(id)
)
USING DELTA
COMMENT 'Attached files (max 1 ENG + 1 ORI per record). file_path points into the UC Volume.';

-- policy_repo_users  ←  Postgres `users`
CREATE TABLE IF NOT EXISTS prd_mega.sgpbpi163.policy_repo_users (
  id STRING NOT NULL,
  name STRING,
  email STRING,
  email_verified TIMESTAMP,
  image STRING,
  password_hash STRING,
  created_at TIMESTAMP,
  CONSTRAINT pk_policy_repo_users PRIMARY KEY (id)
)
USING DELTA
COMMENT 'Local accounts (email + bcrypt hash). Posit Connect identities are used when present.';

-- policy_repo_audit_logs  ←  Postgres `audit_logs`
CREATE TABLE IF NOT EXISTS prd_mega.sgpbpi163.policy_repo_audit_logs (
  id STRING NOT NULL,
  user_id STRING,
  user_email STRING,
  action STRING NOT NULL,
  entity STRING NOT NULL,
  entity_id STRING,
  detail STRING,
  ip STRING,
  created_at TIMESTAMP NOT NULL,
  CONSTRAINT pk_policy_repo_audit_logs PRIMARY KEY (id)
)
USING DELTA
COMMENT 'Create / update / delete audit trail.';

-- policy_repo_partner_api_keys  ←  Postgres `partner_api_keys`
CREATE TABLE IF NOT EXISTS prd_mega.sgpbpi163.policy_repo_partner_api_keys (
  id STRING NOT NULL,
  key_hash STRING NOT NULL,
  key_prefix STRING NOT NULL,
  name STRING NOT NULL,
  contact_email STRING,
  rate_limit_per_minute INT NOT NULL,
  active SMALLINT NOT NULL,
  created_at TIMESTAMP NOT NULL,
  last_used_at TIMESTAMP,
  revoked_at TIMESTAMP,
  notes STRING,
  CONSTRAINT pk_policy_repo_partner_api_keys PRIMARY KEY (id)
)
USING DELTA
COMMENT 'X-API-Key credentials for the plug-in /api/v1 endpoints (sha256 hashes only).';

-- policy_repo_batch_imports  ←  Postgres `batch_imports`
CREATE TABLE IF NOT EXISTS prd_mega.sgpbpi163.policy_repo_batch_imports (
  id STRING NOT NULL,
  file_name STRING,
  status STRING NOT NULL,
  total_rows INT NOT NULL,
  valid_rows INT NOT NULL,
  created_rows INT NOT NULL,
  failed_rows INT NOT NULL,
  auto_translate SMALLINT NOT NULL,
  conflict_policy STRING NOT NULL,
  created_by_email STRING,
  created_at TIMESTAMP NOT NULL,
  updated_at TIMESTAMP NOT NULL,
  CONSTRAINT pk_policy_repo_batch_imports PRIMARY KEY (id)
)
USING DELTA
COMMENT 'Spreadsheet batch uploads (one row per upload).';

-- policy_repo_batch_import_rows  ←  Postgres `batch_import_rows`
CREATE TABLE IF NOT EXISTS prd_mega.sgpbpi163.policy_repo_batch_import_rows (
  id STRING NOT NULL,
  batch_id STRING NOT NULL,
  row_number INT NOT NULL,
  raw STRING NOT NULL,
  status STRING NOT NULL,
  action STRING,
  record_id STRING,
  errors STRING,
  note STRING,
  created_at TIMESTAMP NOT NULL,
  updated_at TIMESTAMP NOT NULL,
  CONSTRAINT pk_policy_repo_batch_import_rows PRIMARY KEY (id),
  CONSTRAINT fk_policy_repo_batch_import_rows_batch_id FOREIGN KEY (batch_id) REFERENCES prd_mega.sgpbpi163.policy_repo_batch_imports(id)
)
USING DELTA
COMMENT 'Per-row progress of a batch upload. raw / errors hold JSON text (was Postgres jsonb).';

-- policy_repo_briefings_cache  ←  Postgres `briefings_cache`
CREATE TABLE IF NOT EXISTS prd_mega.sgpbpi163.policy_repo_briefings_cache (
  id STRING NOT NULL,
  brief_key STRING NOT NULL,
  country STRING NOT NULL,
  lang_type STRING NOT NULL,
  scope STRING NOT NULL,
  content STRING NOT NULL,
  generated_at TIMESTAMP NOT NULL,
  CONSTRAINT pk_policy_repo_briefings_cache PRIMARY KEY (id)
)
USING DELTA
COMMENT 'Latest generated briefing per (brief_key, country, lang_type, scope).';

-- policy_repo_imf_pima_assessments  ←  Postgres `imf_pima_assessments`
CREATE TABLE IF NOT EXISTS prd_mega.sgpbpi163.policy_repo_imf_pima_assessments (
  id STRING NOT NULL,
  iso3 STRING NOT NULL,
  assessment_type STRING NOT NULL,
  title STRING NOT NULL,
  status STRING NOT NULL,
  mission_date DATE NOT NULL,
  year SMALLINT NOT NULL,
  published_at DATE,
  report_url STRING,
  summary_url STRING,
  source_url STRING NOT NULL,
  imf_country_name STRING,
  source STRING NOT NULL,
  reference_url STRING,
  note STRING,
  fetched_at TIMESTAMP NOT NULL,
  created_at TIMESTAMP NOT NULL,
  updated_at TIMESTAMP NOT NULL,
  CONSTRAINT pk_policy_repo_imf_pima_assessments PRIMARY KEY (id),
  CONSTRAINT fk_policy_repo_imf_pima_assessments_iso3 FOREIGN KEY (iso3) REFERENCES prd_mega.sgpbpi163.policy_repo_countries(iso3)
)
USING DELTA
COMMENT 'IMF PIMA / C-PIMA assessments (portal + curated references).';

-- policy_repo_document_chunks  ←  NEW (replaces langchain_pg_collection / langchain_pg_embedding / chunk_parents)
CREATE TABLE IF NOT EXISTS prd_mega.sgpbpi163.policy_repo_document_chunks (
  id STRING NOT NULL,
  record_id STRING NOT NULL,
  document_id STRING,
  country STRING NOT NULL,
  country_name STRING,
  name_eng STRING,
  name_orig STRING,
  year SMALLINT,
  source STRING,
  policy_guidance_tier SMALLINT,
  strategy_tier SMALLINT,
  lang_type STRING,
  lang_code STRING,
  record_status STRING NOT NULL,
  section_heading STRING,
  page INT,
  chunk_index INT NOT NULL,
  content STRING NOT NULL,
  created_at TIMESTAMP NOT NULL,
  CONSTRAINT pk_policy_repo_document_chunks PRIMARY KEY (id),
  CONSTRAINT fk_policy_repo_document_chunks_record_id FOREIGN KEY (record_id) REFERENCES prd_mega.sgpbpi163.policy_repo_policy_records(id)
)
USING DELTA
COMMENT 'RAG text index: one row per ~2000-char section-aware chunk (replaces pgvector tables).';

-- Files (replaces Vercel Blob).  Layout inside the volume:  <ISO3>/<document-id>-<safe file name>
CREATE VOLUME IF NOT EXISTS prd_mega.sgpbpi163.policy_repo_documents
COMMENT 'Policy documents (PDF / DOCX / TXT) – replaces Vercel Blob';
