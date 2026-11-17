"""Edit record – port of `web/app/records/[id]/edit/page.tsx`."""

from __future__ import annotations

from dash import html

from pages import not_found
from pages.pages_constants import RECORD_EDIT_TITLE as TITLE
from utils import queries, record_form
from utils.icons import icon
from utils.routes import href
from utils.ui import link


def layout(params=None):
    rid = (params or {}).get("id")
    record = queries.get_record_with_docs(rid) if rid else None   # always fresh (no caching): no lost updates
    if not record:
        return not_found.layout(params)
    return html.Div([
        html.Div([
            link(icon("arrow-left", "h-3.5 w-3.5"), "Back to Record", href=href("record", id=rid),
                 cls="flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground transition-colors mb-4"),
            html.H1("Edit Record", className="text-2xl font-bold"),
            html.P(record["name_eng"], className="text-muted-foreground text-sm mt-1"),
        ]),
        record_form.record_form("edit", record, record["documents"]),
    ], className="max-w-2xl mx-auto space-y-8")


def register_callbacks(app):
    record_form.register_callbacks(app)
