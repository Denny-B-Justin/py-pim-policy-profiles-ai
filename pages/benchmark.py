"""Benchmark (coming soon) – port of `web/app/benchmark/page.tsx`."""

from __future__ import annotations

from dash import html

from utils.icons import icon
from utils.routes import href
from utils.ui import card, card_content, card_header, card_title, link, link_button

TITLE = "Benchmark — PIM Policy Profiles AI"

DIMENSIONS = [
    "Strategic planning", "Project appraisal", "Project selection", "Budgeting & financing",
    "Implementation & monitoring", "Asset management", "PPP framework", "Transparency & accountability",
]


def layout(params=None):
    return html.Div([
        html.Div([
            link(icon("arrow-left", "h-3.5 w-3.5"), "Back to home", href=href("home"),
                 cls="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"),
            html.H1("Benchmark to frontier and peers", className="mt-3 text-3xl font-bold tracking-tight"),
            html.P("Compare a country's PIM framework against frontier good-practice baselines and selected peers "
                   "across eight PIM dimensions. Powered by hybrid AI extraction and curator-validated scores.",
                   className="mt-2 max-w-2xl text-muted-foreground"),
        ]),
        card(
            card_header(
                html.Div(icon("construction", "h-5 w-5 text-amber-600"),
                         className="flex h-10 w-10 items-center justify-center rounded-lg bg-amber-50"),
                card_title("Coming soon", cls="mt-2"),
            ),
            card_content(
                html.P("The benchmark module is under construction. It will let you:"),
                html.Ul([
                    html.Li("Pick a focal country and 1–3 peers (auto-suggested by region, GDP band, and "
                            "public-investment intensity, or hand-picked)."),
                    html.Li("See a radar chart of scores (1–7) across eight PIM dimensions vs. the frontier baseline."),
                    html.Li("Drill into each dimension to see the underlying policy records and AI-extracted evidence."),
                    html.Li("Generate an AI gap analysis citing specific policy records."),
                ], className="list-disc space-y-1 pl-5"),
                html.Div([
                    html.P("Dimensions covered", className="font-medium text-foreground"),
                    html.Ul([html.Li([html.Span(className="h-1.5 w-1.5 rounded-full bg-amber-500"), d],
                                     className="flex items-center gap-2") for d in DIMENSIONS],
                            className="mt-2 grid gap-1 sm:grid-cols-2"),
                ]),
                html.Div(link_button("Use the AI Coach in the meantime", href=href("coach"), variant="outline"),
                         className="pt-2"),
                cls="space-y-4 text-sm text-muted-foreground",
            ),
            cls="border-dashed bg-muted/30",
        ),
    ], className="space-y-8")
