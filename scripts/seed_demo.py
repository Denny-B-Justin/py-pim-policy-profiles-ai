"""
Seed a LOCAL demo database (sqlite + local file storage) so the UI can be previewed without Databricks.

    PIM_DB_BACKEND=sqlite python scripts/seed_demo.py [--reset]

Never run against Databricks – it refuses to.  Generates small PDFs (fpdf2), stores them, flags text layers,
and builds the search index so the AI Coach / briefings have something to retrieve.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import constants as C  # noqa: E402
from utils import auth, db, documents, imf_pima, indexer, queries, storage  # noqa: E402
from utils.db import table  # noqa: E402
from utils.schema import all_ddl  # noqa: E402

FONT_DIR = C.FONTS_DIR
DEMO_EMAIL = "demo-user@example.org"
DEMO_PASSWORD = "longenough1"

TEXTS = {
    "en": [
        ("Article 1. Purpose and scope",
         "This Act establishes the legal framework for public investment management. It applies to all budgetary "
         "institutions and to projects financed from the state budget, including externally financed projects."),
        ("Article 2. Project appraisal",
         "Every major public investment project shall undergo a cost-benefit appraisal before it is included in the "
         "medium-term budget. The Ministry of Finance issues the appraisal methodology and reviews the analysis."),
        ("Article 3. Project selection and budgeting",
         "Projects are selected through a single project pipeline managed by the Public Investment Unit. Selection "
         "criteria include economic return, alignment with national development strategy, and fiscal affordability."),
        ("Article 4. Implementation and monitoring",
         "Implementing agencies submit quarterly progress reports. Procurement follows the Public Procurement Law. "
         "Cost overruns above 15 percent require re-approval by the Minister of Finance."),
        ("Article 5. Transparency and disclosure",
         "Project information, including appraisal summaries, contracts and progress reports, shall be published on "
         "the open data portal within thirty days. Citizens may submit complaints through the oversight authority."),
        ("Article 6. Evaluation",
         "Ex-post evaluation is conducted for projects above the threshold five years after completion and the "
         "findings feed back into the appraisal methodology."),
    ],
    "lt": [
        ("1 straipsnis. Paskirtis ir taikymo sritis",
         "Šis įstatymas nustato viešųjų investicijų valdymo teisinį pagrindą. Jis taikomas visoms biudžetinėms "
         "įstaigoms ir projektams, finansuojamiems iš valstybės biudžeto."),
        ("2 straipsnis. Projektų vertinimas",
         "Kiekvienas didelis viešųjų investicijų projektas prieš įtraukiant į vidutinio laikotarpio biudžetą "
         "įvertinamas atliekant sąnaudų ir naudos analizę. Finansų ministerija tvirtina vertinimo metodiką."),
        ("3 straipsnis. Skaidrumas",
         "Projekto informacija, įskaitant vertinimo santraukas ir ataskaitas, skelbiama atvirų duomenų portale."),
    ],
    "es": [
        ("Artículo 1. Objeto",
         "La presente ley regula el sistema nacional de inversión pública y establece las reglas para la "
         "formulación, evaluación, priorización y seguimiento de los proyectos de inversión."),
        ("Artículo 2. Evaluación de proyectos",
         "Todo proyecto de inversión pública deberá contar con una evaluación socioeconómica previa realizada "
         "conforme a la metodología del Ministerio de Hacienda."),
        ("Artículo 3. Transparencia",
         "La información de los proyectos será publicada en el portal de datos abiertos del Estado."),
    ],
}

# (country, name_eng, name_orig, year, rev, source, tier, strategy, level, entity, status, link, overview, langs)
# langs: list of (lang_type, lang_code, lang_label, text-key, translation_source)
RECORDS = [
    dict(country="LTU", name_eng="Law on the Budget Structure", name_orig="Biudžeto sandaros įstatymas", year=2014, yr=2023,
         source="Seimas", tier=1, strat=None, link="https://www.e-tar.lt/portal/en/legalAct/TAR.A5A1A22F9EBD",
         overview="Primary budget law governing the preparation, approval and execution of the state budget.",
         docs=[("ORI", "lt", "Lithuanian", "lt", None), ("ENG", "en", "English", "en", "official")]),
    dict(country="LTU", name_eng="Public Investment Programme Methodology", name_orig=None, year=2021, source="Ministry of Finance",
         tier=3, strat=None, link="https://finmin.lrv.lt/en/", overview="Methodology for the appraisal and prioritisation of "
         "public investment projects.", docs=[("ENG", "en", "English", "en", "official")]),
    dict(country="LTU", name_eng="National Progress Strategy Lithuania 2030", name_orig=None, year=2012, source="Government",
         tier=4, strat=1, overview="Long-term national development strategy.", docs=[("ENG", "en", "English", "en", "official")]),
    dict(country="ZAF", name_eng="Public Finance Management Act", name_orig=None, year=1999, yr=2020, source="Parliament",
         tier=1, strat=None, link="https://www.treasury.gov.za/legislation/pfma/", overview="Regulates financial management in the "
         "national and provincial governments.", docs=[("ORI", "en", "English", "en", None)]),
    dict(country="ZAF", name_eng="Framework for Infrastructure Delivery and Procurement Management", name_orig=None, year=2019,
         source="National Treasury", tier=3, strat=None, overview="Guidance for infrastructure delivery management.",
         docs=[("ORI", "en", "English", "en", None)]),
    dict(country="ZAF", name_eng="Cape Town Capital Expenditure Framework", name_orig=None, year=2022, source="City of Cape Town",
         tier=3, strat=6, level="subnational", entity="City of Cape Town", overview="City-level capital investment planning.",
         docs=[("ORI", "en", "English", "en", None)]),
    dict(country="ZAF", name_eng="Treasury Regulations 2005 (superseded)", name_orig=None, year=2005, source="National Treasury",
         tier=2, strat=None, status="archived", overview="Superseded regulations.", docs=[("ORI", "en", "English", "en", None)]),
    dict(country="KEN", name_eng="Public Finance Management Regulations", name_orig=None, year=2015, source="National Treasury",
         tier=2, strat=None, link="https://example.org/this-link-is-broken", overview="Regulations under the PFM Act.", docs=[]),
    dict(country="COL", name_eng="Ley del Sistema General de Regalías", name_orig="Ley del Sistema General de Regalías", year=2012,
         source="Congreso", tier=1, strat=None, overview="Ley que regula el sistema de inversión pública con recursos de regalías.",
         docs=[("ORI", "es", "Spanish", "es", None)]),
    dict(country="COL", name_eng="Public Investment Project Evaluation Guide", name_orig="Guía de evaluación de proyectos", year=2018,
         source="DNP", tier=3, strat=None, overview="Technical guide.", docs=[("ORI", "es", "Spanish", "es", None)],
         scanned=True),
    dict(country="ALB", name_eng="Law on Management of the Budget System", name_orig=None, year=2008, source="Parliament", tier=1,
         strat=None, overview="Albanian budget system law.", docs=[("ENG", "en", "English", "en", "other")]),
    dict(country="IRL", name_eng="Financial Regulation (EU) 2018/1046", name_orig=None, year=2018, source="EU", tier=1, strat=None,
         level="supranational", entity="European Union", overview="Rules applicable to the general budget of the Union.", docs=[]),
]


def make_pdf(title: str, lang: str, scanned: bool = False) -> bytes:
    from fpdf import FPDF

    pdf = FPDF()
    pdf.add_font("DejaVu", "", str(FONT_DIR / "DejaVuSans.ttf"))
    pdf.add_font("DejaVu", "B", str(FONT_DIR / "DejaVuSans-Bold.ttf"))
    pdf.set_auto_page_break(True, 18)
    pdf.add_page()
    if scanned:   # an image-only page: drawn shapes, no text → text-layer detection returns False
        pdf.set_fill_color(230, 230, 230)
        for i in range(8):
            pdf.rect(20, 25 + i * 30, 170, 20, "F")
        return bytes(pdf.output())
    pdf.set_font("DejaVu", "B", 16)
    pdf.multi_cell(0, 9, title, new_x="LMARGIN", new_y="NEXT")
    pdf.ln(3)
    for heading, body in TEXTS[lang]:
        pdf.set_font("DejaVu", "B", 12)
        pdf.multi_cell(0, 7, heading, new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("DejaVu", "", 11)
        pdf.multi_cell(0, 6, body, new_x="LMARGIN", new_y="NEXT")
        pdf.ln(3)
    return bytes(pdf.output())


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reset", action="store_true", help="delete existing demo records/documents first")
    args = ap.parse_args()
    if C.DB_BACKEND != "sqlite":
        sys.exit("seed_demo.py only runs against the local sqlite demo database (PIM_DB_BACKEND=sqlite).")

    for stmt in all_ddl("sqlite"):
        db.execute(stmt)
    # countries (from the reference data)
    if not db.query(f"SELECT 1 FROM {table('countries')} LIMIT 1"):
        for c in json.loads(C.COUNTRIES_JSON.read_text(encoding="utf-8")):
            db.insert("countries", {"iso3": c["iso3"], "name": c["name"], "gdp_2024_bn": c["gdpBn"],
                                    "pub_inv_pct_gdp": c["pubInvPctGdp"], "pub_inv_2024_bn": c["pubInvBn"]})
    if args.reset:
        for t in ("documents", "policy_records", "briefings_cache"):
            db.execute(f"DELETE FROM {table(t)}")
        for f in indexer.chunk_files():
            storage.delete(f["name"], area=C.CHUNKS_DIR)
    if db.query(f"SELECT 1 FROM {table('policy_records')} LIMIT 1") and not args.reset:
        print("records already present – use --reset to rebuild")
    else:
        for spec in RECORDS:
            rec = queries.create_record(dict(
                country=spec["country"], name_eng=spec["name_eng"], name_orig=spec.get("name_orig"), year=spec["year"],
                year_revised=spec.get("yr"), source=spec.get("source"), overview=spec.get("overview"),
                policy_guidance_tier=spec["tier"], strategy_tier=spec["strat"], jurisdiction_level=spec.get("level", "national"),
                jurisdiction_name=spec.get("entity"), record_status=spec.get("status", "latest"), link=spec.get("link")))
            if spec.get("link") and "broken" not in spec["link"]:
                queries.set_link_status(rec["id"], 1, db.now(), db.now())
            elif spec.get("link"):
                queries.set_link_status(rec["id"], 0, db.now(), None)
            for lang_type, code, label, text_key, src in spec["docs"]:
                data = make_pdf(spec["name_eng"], text_key, spec.get("scanned", False))
                documents.attach_document(rec["id"], lang_type, f"{spec['name_eng'].replace(' ', '_')}_{lang_type}.pdf", data,
                                          lang_code=code, lang_label=label, translation_source=src, index=False)
        print(f"seeded {len(RECORDS)} records")
    done = indexer.reindex_all()
    print("index:", done)
    # a couple of PIMA rows so the Countries table shows markers
    if not db.query(f"SELECT 1 FROM {table('imf_pima_assessments')} LIMIT 1"):
        imf_pima.apply_references()
    # a demo account (the first account is always allowed; later ones need ADMIN_INVITE_CODE)
    if not db.query(f"SELECT 1 FROM {table('users')} LIMIT 1"):
        ok, err = auth.register("Demo User", DEMO_EMAIL, DEMO_PASSWORD, None)
        print(f"demo login: {DEMO_EMAIL} / {DEMO_PASSWORD}" if ok else f"demo user not created: {err}")
    db.invalidate("")


if __name__ == "__main__":
    main()
