"""
Markdown → Word / PDF export – port of `rag/app/export/{docx_export,pdf_export}.py`.

The two exporters share the original's tiny markdown dialect (## / ### headings, pipe tables, "- " bullets,
**bold**) and are parameterised by a theme: "profile" (blue) for country briefings and policy-change reports,
"transparency" (green) for transparency briefings.  Both return `(bytes, mimetype, filename)`.
"""

from __future__ import annotations

import io
import logging
import re
from dataclasses import dataclass
import constants as C

log = logging.getLogger(__name__)

FONTS_DIR = C.FONTS_DIR
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
PDF_MIME = "application/pdf"
MAX_CONTENT = C.BRIEFING_MAX_CONTENT

# C0 control characters illegal in OOXML (python-docx raises on them; LLM output can contain a stray form feed).
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


@dataclass(frozen=True)
class Theme:
    header_label: str = "PIM Country Briefing"  # PDF running header
    title_prefix: str = "Public Investment Management Context"
    filename_prefix: str = "PIM_Profile"
    accent_rgb: tuple[int, int, int] = (0x1D, 0x4E, 0xD8)  # blue
    header_fill_hex: str = "1d4ed8"  # Word table-header shading


PROFILE_THEME = Theme()
TRANSPARENCY_THEME = Theme(header_label="PIM Transparency Briefing", title_prefix="PIM Transparency Briefing",
                           filename_prefix="PIM_Transparency", accent_rgb=(0x0E, 0x7C, 0x47), header_fill_hex="0e7c47")
THEMES = {"profile": PROFILE_THEME, "transparency": TRANSPARENCY_THEME}

_TABLE_SEP = re.compile(r"^\|[\s\-:|]+\|$")
_BULLET_MARK = re.compile(r"^[-*]\s+")  # the original used lstrip("-* "), which also ate a leading ** bold marker


def _is_table_line(line: str) -> bool:
    s = line.strip()
    return s.startswith("|") and s.endswith("|")


def _bullet(line: str) -> bool:
    s = line.strip()
    return s.startswith("- ") or s.startswith("* ")


def safe_filename(name: str) -> str:
    """The original only swapped spaces; also drop path/quote characters so the name is header-safe."""
    return re.sub(r'[\\/:*?"<>|\r\n]+', "", name.replace(" ", "_"))


# ── Word ─────────────────────────────────────────────────────────────────────
def export_docx(content: str, country_name: str, theme: Theme = PROFILE_THEME) -> tuple[bytes, str, str]:
    from docx import Document  # noqa: PLC0415
    from docx.shared import Inches, Pt, RGBColor  # noqa: PLC0415

    content = _CONTROL_CHARS.sub("", content)
    doc = Document()

    section = doc.sections[0]  # A4, 1" margins
    section.page_width, section.page_height = Inches(8.27), Inches(11.69)
    section.top_margin = section.bottom_margin = section.left_margin = section.right_margin = Inches(1)

    title = doc.add_heading(f"{theme.title_prefix}: {country_name}", level=1)
    title.runs[0].font.size = Pt(16)
    title.runs[0].font.color.rgb = RGBColor(*theme.accent_rgb)

    table_rows: list[list[str]] = []

    def flush():
        nonlocal table_rows
        if table_rows:
            _docx_table(doc, table_rows, theme)
            table_rows = []

    for line in content.split("\n"):
        if line.startswith("## "):
            flush()
            doc.add_heading(line[3:].strip(), level=2)
        elif line.startswith("### "):
            flush()
            doc.add_heading(line[4:].strip(), level=3)
        elif _is_table_line(line):
            if _TABLE_SEP.match(line.strip()):
                continue
            table_rows.append([c.strip() for c in line.strip().strip("|").split("|")])
        elif _bullet(line):
            flush()
            p = doc.add_paragraph(style="List Bullet")
            _docx_runs(p, _BULLET_MARK.sub("", line.strip()))
        elif line.strip().startswith("**") and line.strip().endswith("**"):
            flush()
            p = doc.add_paragraph()
            p.add_run(line.strip().strip("*")).bold = True
        elif line.strip():
            flush()
            _docx_runs(doc.add_paragraph(), line.strip())
        else:
            flush()
    flush()

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue(), DOCX_MIME, f"{theme.filename_prefix}_{safe_filename(country_name)}.docx"


def _docx_table(doc, rows: list[list[str]], theme: Theme) -> None:
    from docx.oxml.ns import qn  # noqa: PLC0415
    from docx.shared import Pt, RGBColor  # noqa: PLC0415

    num_cols = max(len(r) for r in rows)
    table = doc.add_table(rows=len(rows), cols=num_cols)
    table.style = "Table Grid"
    for ri, row in enumerate(rows):
        for ci, text in enumerate(row):
            if ci >= num_cols:
                continue
            cell = table.rows[ri].cells[ci]
            cell.text = ""
            run = cell.paragraphs[0].add_run(re.sub(r"\*\*(.*?)\*\*", r"\1", text))
            run.font.size = Pt(9)
            if ri == 0:
                run.bold = True
                run.font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
                tc_pr = cell._element.get_or_add_tcPr()
                tc_pr.append(tc_pr.makeelement(qn("w:shd"), {qn("w:val"): "clear", qn("w:color"): "auto",
                                                              qn("w:fill"): theme.header_fill_hex}))
    doc.add_paragraph()


def _docx_runs(paragraph, text: str) -> None:
    from docx.shared import Pt  # noqa: PLC0415

    for part in re.split(r"(\*\*.*?\*\*)", text):
        bold = part.startswith("**") and part.endswith("**") and len(part) >= 4
        run = paragraph.add_run(part[2:-2] if bold else part)
        run.bold = True if bold else None
        run.font.size = Pt(10)


# ── PDF ──────────────────────────────────────────────────────────────────────
# Control characters, zero-widths and the emoji variation selector that no font should receive.
_PDF_STRIP = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f​‌‍️﻿]")
# Template emoji (e.g. the transparency rating scale) that DejaVu lacks → dingbats it does cover.
_EMOJI_TO_GLYPH = {"✅": "✓", "❌": "✗", "\U0001f7e2": "●", "\U0001f534": "●",
                   "\U0001f7e1": "●"}
_LATIN1_FALLBACK = {"—": "--", "–": "-", "‘": "'", "’": "'", "“": '"', "”": '"',
                    "…": "...", "•": "-"}


def _register_fonts(pdf) -> str | None:
    """Embedded Unicode fonts (DejaVu Sans: Latin/Cyrillic/Georgian/Armenian; NanumGothic: Korean fallback)."""
    dejavu = FONTS_DIR / "DejaVuSans.ttf"
    if not dejavu.exists():
        log.warning("Unicode fonts missing at %s – falling back to Helvetica", FONTS_DIR)
        return None
    pdf.add_font("PIMSans", "", dejavu)
    pdf.add_font("PIMSans", "B", FONTS_DIR / "DejaVuSans-Bold.ttf")
    pdf.add_font("PIMSans", "I", FONTS_DIR / "DejaVuSans-Oblique.ttf")
    nanum = FONTS_DIR / "NanumGothic-Regular.ttf"
    if nanum.exists():
        pdf.add_font("NanumGothic", "", nanum)
        pdf.add_font("NanumGothic", "B", FONTS_DIR / "NanumGothic-Bold.ttf")
        pdf.set_fallback_fonts(["NanumGothic"], exact_match=False)
    return "PIMSans"


def _sanitize_pdf(text: str, unicode_ok: bool = True) -> str:
    text = _PDF_STRIP.sub("", text).replace(" ", " ")
    for ch, rep in _EMOJI_TO_GLYPH.items():
        text = text.replace(ch, rep)
    if unicode_ok:
        return text
    for ch, rep in _LATIN1_FALLBACK.items():
        text = text.replace(ch, rep)
    return text.encode("latin-1", errors="replace").decode("latin-1")


def export_pdf(content: str, country_name: str, theme: Theme = PROFILE_THEME) -> tuple[bytes, str, str]:
    from fpdf import FPDF  # noqa: PLC0415

    pdf = FPDF(orientation="P", unit="mm", format="A4")
    pdf.core_fonts_encoding = "windows-1252"  # only used on the core-font fallback (needs the bullet marker)
    family = _register_fonts(pdf) or "Helvetica"
    unicode_ok = family != "Helvetica"
    country_name = _sanitize_pdf(country_name, unicode_ok)
    content = _sanitize_pdf(content, unicode_ok)

    pdf.set_auto_page_break(auto=True, margin=25)
    pdf.add_page()
    r, g, b = theme.accent_rgb

    pdf.set_font(family, "I", 8)
    pdf.set_text_color(128, 128, 128)
    pdf.cell(0, 5, f"{theme.header_label} - {country_name}", align="R", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(5)
    pdf.set_font(family, "B", 16)
    pdf.set_text_color(r, g, b)
    pdf.multi_cell(0, 8, f"{theme.title_prefix}: {country_name}", new_x="LMARGIN", new_y="NEXT")  # wraps long titles
    pdf.ln(2)
    pdf.set_draw_color(r, g, b)
    pdf.line(10, pdf.get_y(), 200, pdf.get_y())
    pdf.ln(8)
    pdf.set_text_color(0, 0, 0)

    table_rows: list[list[str]] = []

    def flush():
        nonlocal table_rows
        if table_rows:
            _pdf_table(pdf, table_rows, theme, family)
            table_rows = []

    for line in content.split("\n"):
        if line.startswith("## "):
            flush()
            pdf.ln(4)
            pdf.set_font(family, "B", 12)
            pdf.cell(0, 7, line[3:].strip(), new_x="LMARGIN", new_y="NEXT")
            pdf.ln(2)
        elif line.startswith("### "):
            flush()
            pdf.ln(2)
            pdf.set_font(family, "B", 10)
            pdf.cell(0, 6, line[4:].strip(), new_x="LMARGIN", new_y="NEXT")
            pdf.ln(1)
        elif _is_table_line(line):
            if _TABLE_SEP.match(line.strip()):
                continue
            table_rows.append([c.strip() for c in line.strip().strip("|").split("|")])
        elif _bullet(line):
            flush()
            text = re.sub(r"\*\*(.*?)\*\*", r"\1", _BULLET_MARK.sub("", line.strip()))
            pdf.set_font(family, "", 9)
            pdf.cell(5, 5, chr(8226))
            pdf.multi_cell(0, 5, f" {text}", new_x="LMARGIN", new_y="NEXT")
        elif line.strip():
            flush()
            pdf.set_font(family, "", 9)
            pdf.multi_cell(0, 5, re.sub(r"\*\*(.*?)\*\*", r"\1", line.strip()), new_x="LMARGIN", new_y="NEXT")
        else:
            flush()
            pdf.ln(2)
    flush()

    pdf.set_y(-15)
    pdf.set_font(family, "", 8)
    pdf.cell(0, 10, f"Page {pdf.page_no()}", align="C")
    return bytes(pdf.output()), PDF_MIME, f"{theme.filename_prefix}_{safe_filename(country_name)}.pdf"


def _pdf_table(pdf, rows: list[list[str]], theme: Theme, family: str) -> None:
    r, g, b = theme.accent_rgb
    num_cols = max(len(row) for row in rows)
    col_w = (pdf.w - pdf.l_margin - pdf.r_margin) / num_cols
    for ri, row in enumerate(rows):
        if ri == 0:
            pdf.set_fill_color(r, g, b)
            pdf.set_text_color(255, 255, 255)
            pdf.set_font(family, "B", 8)
        else:
            pdf.set_fill_color(255, 255, 255)
            pdf.set_text_color(0, 0, 0)
            pdf.set_font(family, "", 8)
        cells = [re.sub(r"\*\*(.*?)\*\*", r"\1", row[i] if i < len(row) else "") for i in range(num_cols)]
        row_h = max([6] + [max(1, len(t) // max(1, int(col_w / 2)) + 1) * 5 for t in cells])
        x0, y0 = pdf.get_x(), pdf.get_y()
        if y0 + row_h > pdf.h - pdf.b_margin:
            pdf.add_page()
            y0 = pdf.get_y()
        for ci, t in enumerate(cells):
            pdf.set_xy(x0 + ci * col_w, y0)
            pdf.multi_cell(col_w, 5, t, border=1, fill=(ri == 0), new_x="RIGHT", new_y="TOP")
        pdf.set_xy(x0, y0 + row_h)
    pdf.ln(4)


# ── dispatcher used by the API route and the pages ───────────────────────────
def export(fmt: str, content: str, name: str, theme: str = "profile") -> tuple[bytes, str, str]:
    if len(content) > MAX_CONTENT:
        raise ValueError("Content too large to export.")
    th = THEMES.get(theme, PROFILE_THEME)
    if fmt == "docx":
        return export_docx(content, name, th)
    if fmt == "pdf":
        return export_pdf(content, name, th)
    raise ValueError("Format must be 'docx' or 'pdf'")
