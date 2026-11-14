# Embedded PDF fonts

Vendored so original-language briefings export to PDF with correct glyphs.
fpdf2 subsets these on output, so generated PDFs stay small.

| Font | Files | Covers | License |
|---|---|---|---|
| DejaVu Sans 2.37 | `DejaVuSans.ttf`, `-Bold`, `-Oblique` | Latin (incl. all corpus diacritics), Cyrillic, Georgian, Armenian, dingbats | [DejaVu / Bitstream Vera license](https://dejavu-fonts.github.io/License.html) — free to redistribute |
| Nanum Gothic | `NanumGothic-Regular.ttf`, `-Bold` | Korean (Hangul) | [SIL Open Font License 1.1](https://scripts.sil.org/OFL) |

Sources: dejavu-fonts.github.io release 2.37; google/fonts `ofl/nanumgothic`.
NanumGothic is registered as an fpdf2 per-glyph fallback in
`app/export/pdf_export.py` (`_register_unicode_fonts`).
