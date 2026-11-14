"""Release notes – port of `web/app/release-notes/page.tsx` (data in data/release_notes.json)."""

from __future__ import annotations

import json
from datetime import datetime

from dash import html

from utils import constants as C
from utils.ui import badge, separator

TITLE = "Release notes · PIM Policy Profiles AI"

CHANGE_TYPE_STYLES = {
    "feature": dict(label="New", cls="bg-blue-50 text-blue-700 border-blue-200", dot="bg-blue-500"),
    "improvement": dict(label="Improved", cls="bg-violet-50 text-violet-700 border-violet-200", dot="bg-violet-500"),
    "fix": dict(label="Fixed", cls="bg-emerald-50 text-emerald-700 border-emerald-200", dot="bg-emerald-500"),
    "breaking": dict(label="Breaking", cls="bg-red-50 text-red-700 border-red-200", dot="bg-red-500"),
}


def _releases() -> list[dict]:
    with open(C.BASE_DIR / "data" / "release_notes.json", encoding="utf-8") as f:
        return json.load(f)


def _format_date(s: str) -> str:
    d = datetime.strptime(s, "%Y-%m-%d")
    return f"{d.strftime('%B')} {d.day}, {d.year}"


def layout(params=None):
    releases = _releases()
    articles = []
    for i, rel in enumerate(releases):
        items = []
        for ch in rel["changes"]:
            st = CHANGE_TYPE_STYLES[ch["type"]]
            items.append(html.Li([
                html.Span(className=f"mt-1.5 h-1.5 w-1.5 rounded-full shrink-0 {st['dot']}"),
                html.Div([
                    badge(st["label"], variant="outline", cls=f"text-[10px] px-1.5 py-0 shrink-0 mt-0.5 {st['cls']}"),
                    html.Span(ch["text"], className="text-sm text-foreground/90 leading-snug"),
                ], className="flex items-start gap-2 flex-wrap"),
            ], className="flex items-start gap-3"))
        articles.append(html.Article([
            html.Div([
                html.Div([
                    html.Span(rel["version"], className="text-xs font-semibold uppercase tracking-widest text-muted-foreground"),
                    badge("Latest", variant="outline", cls="text-[10px] px-2 py-0 bg-blue-50 text-blue-700 border-blue-200")
                    if i == 0 else None,
                ], className="flex items-center gap-3"),
                html.Time(_format_date(rel["date"]), dateTime=rel["date"], className="text-xs text-muted-foreground"),
            ], className="flex items-center justify-between mb-3"),
            html.Div([
                html.Div([
                    html.H2(rel["title"], className="text-lg font-semibold"),
                    html.P(rel["description"], className="text-sm text-muted-foreground leading-relaxed"),
                ], className="space-y-1.5"),
                separator(),
                html.Ul(items, className="space-y-2.5"),
            ], className="rounded-xl border bg-muted/30 p-6 space-y-5"),
            html.Div(className="absolute left-0 -bottom-5 w-px h-5 bg-border ml-0") if i < len(releases) - 1 else None,
        ], className="group relative"))

    return html.Div([
        html.Div([
            html.H1("Release notes", className="text-3xl font-bold tracking-tight"),
            html.P("New updates and improvements to the PIM Country Policy Profile Repository. Each release note "
                   "documents what changed, what was added, and any fixes applied to the platform.",
                   className="text-muted-foreground leading-relaxed max-w-xl"),
        ], className="space-y-3"),
        separator(),
        html.Div(articles, className="space-y-8"),
    ], className="max-w-3xl mx-auto space-y-10")
