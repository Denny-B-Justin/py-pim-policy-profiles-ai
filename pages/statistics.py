"""Statistics – port of `web/app/statistics/{page,StatisticsContent}.tsx` + `components/records/LanguageBreakdown.tsx`.

Recharts is not available in Dash, so the four charts are Plotly figures styled to match the Recharts originals
(same colours, fonts, sizes, dashed grid, rounded bars, donut proportions).  The page shows the same
“Loading statistics…” state, error alert with *Retry*, and *Refresh* button as the original.
"""

from __future__ import annotations

import logging
import time

from dash import Input, Output, dcc, html, no_update

from utils import queries, rag
from utils.icons import icon
from utils.ui import cn, loading_spinner

log = logging.getLogger(__name__)

TITLE = "Search Index Analytics — PIM Policy Profiles AI"

# ── colours (StatisticsContent.tsx) + the shadcn tokens Plotly cannot read from CSS variables ──
BLUE = "#1d4ed8"
BLUE_LIGHT = "#3b82f6"
TIER_COLORS = ["#1d4ed8", "#2563eb", "#3b82f6", "#60a5fa"]
LANG_COLORS = ["#1d4ed8", "#10b981"]
MUTED_FG = "#737373"      # --muted-foreground
BORDER = "#e5e5e5"        # --border
FG = "#0a0a0a"            # --foreground
AXIS = "#666666"          # Recharts' default axis stroke
FONT = "Inter, ui-sans-serif, system-ui, sans-serif"

CONTAINER = "mx-auto max-w-5xl px-4 py-16"
CARD = "rounded-lg border bg-background p-5 shadow-sm"
H3 = "mb-4 text-sm font-semibold uppercase tracking-wide text-muted-foreground"
EMPTY = "py-12 text-center text-sm text-muted-foreground"


def _n(v) -> str:
    return f"{int(v):,}"


# ── figures ─────────────────────────────────────────────────────────────────────────────
def _base_layout(height: int, **extra) -> dict:
    layout = {
        "height": height,
        "paper_bgcolor": "rgba(0,0,0,0)", "plot_bgcolor": "rgba(0,0,0,0)",
        "font": {"family": FONT, "size": 11, "color": MUTED_FG},
        "hoverlabel": {"bgcolor": "#ffffff", "bordercolor": BORDER, "font": {"family": FONT, "size": 12, "color": FG}},
        "dragmode": False, "showlegend": False,
        "margin": {"l": 5, "r": 5, "t": 5, "b": 5},
    }
    layout.update(extra)
    return layout


def _axis(**extra) -> dict:
    ax = {"showgrid": True, "gridcolor": BORDER, "griddash": "3px,3px", "gridwidth": 1, "zeroline": False,
          "showline": True, "linecolor": AXIS, "linewidth": 1, "ticks": "outside", "ticklen": 6, "tickcolor": AXIS,
          "tickfont": {"size": 11, "color": MUTED_FG, "family": FONT}, "fixedrange": True, "automargin": True}
    ax.update(extra)
    return ax


def _graph(figure: dict, height: int, **props):
    return dcc.Graph(figure=figure, config={"displayModeBar": False, "responsive": True, "staticPlot": False},
                     style={"height": f"{height}px", "width": "100%"}, **props)


def country_figure(by_country: list[dict]):
    height = max(300, len(by_country) * 32)
    fig = {
        "data": [{
            "type": "bar", "orientation": "h", "name": "documents",
            "x": [c["documents"] for c in by_country], "y": [c["country"] for c in by_country],
            "customdata": [c["country_name"] or c["country"] for c in by_country],
            "marker": {"color": BLUE, "cornerradius": 4},
            "hovertemplate": "<b>%{customdata}</b><br>Documents: %{x:,}<extra></extra>",
        }],
        "layout": _base_layout(
            height, margin={"l": 10, "r": 20, "t": 5, "b": 5}, bargap=0.2,
            xaxis=_axis(type="linear", rangemode="tozero"),
            yaxis=_axis(type="category", autorange="reversed", showgrid=True, ticklen=6),
        ),
    }
    return _graph(fig, height, id="stats-country-chart")


def tier_figure(by_tier: list[dict]):
    fig = {
        "data": [{
            "type": "pie", "labels": [t["label"] for t in by_tier], "values": [t["documents"] for t in by_tier],
            "hole": 0.5, "sort": False, "direction": "counterclockwise", "rotation": 90,
            "marker": {"colors": [TIER_COLORS[i % len(TIER_COLORS)] for i in range(len(by_tier))],
                       "line": {"color": "#ffffff", "width": 2}},
            "texttemplate": "%{value}", "textposition": "outside",
            "textfont": {"size": 12, "color": FG, "family": FONT},
            "hovertemplate": "%{label}: %{value:,}<extra></extra>",
        }],
        "layout": _base_layout(300, margin={"l": 10, "r": 10, "t": 60, "b": 60}),
    }
    return _graph(fig, 300, id="stats-tier-chart")


def year_figure(by_year: list[dict]):
    fig = {
        "data": [{
            "type": "bar", "x": [y["year"] for y in by_year], "y": [y["documents"] for y in by_year],
            "marker": {"color": BLUE_LIGHT, "cornerradius": 4},
            "hovertemplate": "%{x}<br>Documents: %{y:,}<extra></extra>",
        }],
        "layout": _base_layout(
            300, margin={"l": 5, "r": 5, "t": 5, "b": 20}, bargap=0.2,
            xaxis=_axis(type="category", tickangle=-45),
            yaxis=_axis(type="linear", rangemode="tozero"),
        ),
    }
    return _graph(fig, 300, id="stats-year-chart")


def language_figure(lang_data: list[tuple[str, int]]):
    fig = {
        "data": [{
            "type": "pie", "labels": [n for n, _ in lang_data], "values": [v for _, v in lang_data],
            "hole": 0.5, "sort": False, "direction": "counterclockwise", "rotation": 90, "textinfo": "none",
            "marker": {"colors": [LANG_COLORS[i % len(LANG_COLORS)] for i in range(len(lang_data))],
                       "line": {"color": "#ffffff", "width": 3}},
            "hovertemplate": "%{label}: %{value:,} documents<extra></extra>",
        }],
        "layout": _base_layout(
            250, showlegend=True, margin={"l": 10, "r": 10, "t": 25, "b": 25},
            legend={"orientation": "h", "x": 0.5, "xanchor": "center", "y": 0, "yanchor": "top",
                    "font": {"size": 12, "color": FG, "family": FONT}},
        ),
    }
    return _graph(fig, 250, id="stats-lang-chart")


# ── pieces ──────────────────────────────────────────────────────────────────────────────
def _stat_card(label: str, value):
    shown = _n(value) if isinstance(value, int) else value
    return html.Div([
        html.P(label, className="text-xs font-medium uppercase tracking-wide text-muted-foreground"),
        html.P(shown, className="mt-2 text-2xl font-bold tabular-nums"),
    ], className="rounded-lg border bg-muted/40 p-5")


def _pill(label: str, value: str):
    return html.Span([html.Span(f"{label}:", className="font-medium text-foreground"), value],
                     className="inline-flex items-center gap-1.5 rounded-full border bg-muted/60 px-3 py-1 "
                               "text-xs text-muted-foreground")


def _card(title: str, body, cls: str = ""):
    return html.Div([html.H3(title, className=H3), body], className=cn(CARD, cls))


def _heading():
    return html.H2("Search Index Analytics", className="mb-6 text-3xl font-bold")


def loading_view():
    return html.Div([
        _heading(),
        html.Div([loading_spinner(), "Loading statistics…"],
                 className="flex items-center gap-2 text-sm text-muted-foreground"),
    ], className=CONTAINER)


def error_view(message: str):
    return html.Div([
        _heading(),
        html.Div(message, role="alert",
                 className="rounded-md border border-red-300 bg-red-50 p-4 text-sm text-red-800"),
        html.Button("Retry", id="stats-refresh", n_clicks=0,
                    className="mt-4 rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground "
                              "hover:opacity-90"),
    ], className=CONTAINER)


def language_breakdown():
    """components/records/LanguageBreakdown.tsx – fails quiet (returns None) like the original."""
    try:
        data = queries.get_language_breakdown()
    except Exception:  # noqa: BLE001
        log.exception("language breakdown failed")
        return None
    overall, rows = data["overall"], [r for r in data["by_country"] if r["ori_total"] > 0]
    stats = [("Original-language documents", overall["ori_total"]), ("Original is English", overall["ori_english"]),
             ("Non-English documents", overall["ori_non_english"]), ("English translations", overall["eng_translations"])]
    intro = ("Each policy can have an original-language document and/or an English translation. “Original is English” "
             "counts originals already written in English (e.g. English-speaking countries).")
    if overall["ori_unknown"] > 0:
        intro += f" {overall['ori_unknown']} original document(s) have no language recorded."
    th = "px-4 py-2.5 font-medium text-muted-foreground"

    def dash(v):
        return str(v) if v else "—"

    return html.Section([
        html.H3("Documents by Original Language", className=H3.replace("mb-4", "mb-1")),
        html.P(intro, className="mb-4 text-xs text-muted-foreground"),
        html.Div([html.Div([html.Div(str(v), className="text-2xl font-bold tabular-nums"),
                            html.Div(lbl, className="mt-0.5 text-xs text-muted-foreground")],
                           className="rounded-lg border bg-background p-4 shadow-sm") for lbl, v in stats],
                 className="grid grid-cols-2 gap-3 sm:grid-cols-4"),
        html.Div(html.Table([
            html.Thead(html.Tr([
                html.Th("Country", className=cn(th, "text-left")),
                html.Th("Original docs", className=cn(th, "text-right")),
                html.Th("Original is English", className=cn(th, "text-right")),
                html.Th("Non-English", className=cn(th, "text-right")),
                html.Th("English translations", className=cn(th, "text-right")),
            ], className="border-b bg-muted/40 text-left")),
            html.Tbody([html.Tr([
                html.Td([r["name"], html.Span(r["iso3"], className="ml-1.5 font-mono text-xs text-muted-foreground")],
                        className="px-4 py-2"),
                html.Td(str(r["ori_total"]), className="px-4 py-2 text-right tabular-nums"),
                html.Td(dash(r["ori_english"]), className="px-4 py-2 text-right tabular-nums"),
                html.Td(dash(r["ori_non_english"]), className="px-4 py-2 text-right tabular-nums"),
                html.Td(dash(r["eng_translations"]), className="px-4 py-2 text-right tabular-nums"),
            ], className="border-b last:border-0") for r in rows]),
            html.Tfoot(html.Tr([
                html.Td("Total", className="px-4 py-2.5"),
                html.Td(str(overall["ori_total"]), className="px-4 py-2.5 text-right tabular-nums"),
                html.Td(str(overall["ori_english"]), className="px-4 py-2.5 text-right tabular-nums"),
                html.Td(str(overall["ori_non_english"]), className="px-4 py-2.5 text-right tabular-nums"),
                html.Td(str(overall["eng_translations"]), className="px-4 py-2.5 text-right tabular-nums"),
            ], className="border-t-2 font-semibold")),
        ], className="w-full text-sm"), className="mt-5 overflow-x-auto rounded-lg border"),
    ], className="mt-10")


def content_view(data: dict):
    summary, by_country, by_tier, by_year = data["summary"], data["by_country"], data["by_tier"], data["by_year"]
    lang_data = [(n, v) for n, v in (("English", summary["lang_eng_documents"]),
                                     ("Original Language", summary["lang_ori_documents"])) if v > 0]

    th = "px-5 py-3 font-medium text-muted-foreground"
    avg = lambda ch, d: f"{ch / d:.1f}" if d > 0 else "—"  # noqa: E731

    country_chart = (country_figure(by_country) if by_country
                     else html.P("No country data available.", className=EMPTY))
    tier_chart = tier_figure(by_tier) if by_tier else html.P("No tier data available.", className=EMPTY)
    year_chart = year_figure(by_year) if by_year else html.P("No year data available.", className=EMPTY)
    lang_chart = language_figure(lang_data) if lang_data else html.P("No language data available.", className=EMPTY)

    children = [
        # Header
        html.Div([
            html.Div([
                html.H2("Search Index Analytics", className="text-3xl font-bold"),
                html.P(["Real-time overview of the document collection stored in the search index. Retrieval engine: ",
                        html.Span(data["search_engine"], className="font-mono text-xs")],
                       className="mt-1 text-muted-foreground"),
            ]),
            html.Button([icon("refresh-cw", "h-3.5 w-3.5"), "Refresh"], id="stats-refresh", n_clicks=0,
                        className="inline-flex items-center gap-2 rounded-md border px-4 py-2 text-sm font-medium "
                                  "text-foreground transition-colors hover:bg-muted"),
        ], className="mb-8 flex flex-wrap items-start justify-between gap-4"),
        # Summary cards
        html.Div([
            _stat_card("Documents Indexed", summary["total_documents"]),
            _stat_card("Text Chunks", summary["total_chunks"]),
            _stat_card("Total Tokens", summary["total_tokens"] if summary["total_tokens"] is not None else "—"),
            _stat_card("Countries", len(by_country)),
        ], className="grid grid-cols-2 gap-4 sm:grid-cols-4"),
        # Config pills
        html.Div([
            _pill("Index Store", data["vector_store"]),
            _pill("Chunk Size", f"{data['chunk_size']} chars"),
            _pill("Overlap", f"{data['chunk_overlap']} chars"),
            _pill("Retriever", data["retriever_type"]),
        ], className="mt-4 flex flex-wrap gap-2"),
        language_breakdown(),
        # Charts row 1
        html.Div([
            _card("Documents by Country", country_chart, "lg:col-span-2"),
            html.Div([
                html.H3("Documents by Policy Tier", className=H3),
                tier_chart,
                html.Div([html.Div([
                    html.Span(className="inline-block h-2.5 w-2.5 rounded-full",
                              style={"backgroundColor": TIER_COLORS[i % len(TIER_COLORS)]}),
                    f"{t['label']} ({t['documents']})",
                ], className="flex items-center gap-2 text-xs text-muted-foreground") for i, t in enumerate(by_tier)],
                    className="mt-2 space-y-1"),
            ], className=CARD),
        ], className="mt-10 grid grid-cols-1 gap-6 lg:grid-cols-3"),
        # Charts row 2
        html.Div([
            _card("Documents by Year", year_chart, "lg:col-span-2"),
            html.Div([
                html.H3("Language Distribution", className=H3),
                lang_chart,
                html.Div(f"{summary['lang_eng_documents']} English · {summary['lang_ori_documents']} Original",
                         className="mt-2 text-center text-xs text-muted-foreground"),
            ], className=CARD),
        ], className="mt-6 grid grid-cols-1 gap-6 lg:grid-cols-3"),
    ]
    if by_country:
        children.append(html.Div([
            html.Div(html.H3("Country Detail", className=H3.replace("mb-4 ", "")), className="px-5 py-4 border-b"),
            html.Div(html.Table([
                html.Thead(html.Tr([
                    html.Th("Country", className=cn(th, "text-left")), html.Th("ISO3", className=cn(th, "text-left")),
                    html.Th("Documents", className=cn(th, "text-right")),
                    html.Th("Chunks", className=cn(th, "text-right")),
                    html.Th("Avg Chunks/Doc", className=cn(th, "text-right")),
                ], className="border-b bg-muted")),
                html.Tbody([html.Tr([
                    html.Td(c["country_name"] or c["country"], className="px-5 py-3 font-medium"),
                    html.Td(c["country"], className="px-5 py-3 font-mono text-xs text-muted-foreground"),
                    html.Td(_n(c["documents"]), className="px-5 py-3 text-right tabular-nums"),
                    html.Td(_n(c["chunks"]), className="px-5 py-3 text-right tabular-nums"),
                    html.Td(avg(c["chunks"], c["documents"]), className="px-5 py-3 text-right tabular-nums"),
                ], className="border-b last:border-0 hover:bg-muted/50 transition-colors") for c in by_country]),
                html.Tfoot(html.Tr([
                    html.Td("Total", colSpan=2, className="px-5 py-3"),
                    html.Td(_n(summary["total_documents"]), className="px-5 py-3 text-right tabular-nums"),
                    html.Td(_n(summary["total_chunks"]), className="px-5 py-3 text-right tabular-nums"),
                    html.Td(avg(summary["total_chunks"], summary["total_documents"]),
                            className="px-5 py-3 text-right tabular-nums"),
                ], className="bg-muted font-medium")),
            ], className="w-full text-sm"), className="overflow-x-auto"),
        ], className="mt-10 rounded-lg border bg-background shadow-sm overflow-hidden"))
    return html.Div([c for c in children if c is not None], className=CONTAINER)


# ── page ────────────────────────────────────────────────────────────────────────────────
def layout(params=None):
    return html.Div([
        dcc.Store(id="stats-tick", data=0),
        html.Div(loading_view(), id="stats-root"),
    ])


def register_callbacks(app):
    @app.callback(Output("stats-root", "children"), Input("stats-tick", "data"))
    def render(tick):
        try:
            return content_view(rag.stats_detail(force=bool(tick)))
        except Exception:  # noqa: BLE001
            log.exception("statistics failed")
            return error_view("Failed to load statistics. Please try again.")

    @app.callback(
        Output("stats-tick", "data"), Output("stats-root", "children", allow_duplicate=True),
        Input("stats-refresh", "n_clicks"), prevent_initial_call=True,
    )
    def refresh(n):
        if not n:
            return no_update, no_update
        return time.time(), loading_view()
