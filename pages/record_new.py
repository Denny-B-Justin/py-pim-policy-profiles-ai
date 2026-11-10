"""New record – port of `web/app/records/new/page.tsx`."""

from __future__ import annotations

from dash import html

from utils import record_form
from utils.icons import icon
from utils.routes import href
from utils.ui import link

TITLE = "Add New Policy Record — PIM Policy Profiles AI"


def layout(params=None):
    return html.Div([
        html.Div([
            link(icon("arrow-left", "h-3.5 w-3.5"), "Back to Repository", href=href("repository"),
                 cls="flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground transition-colors mb-4"),
            html.H1("Add New Policy Record", className="text-2xl font-bold"),
            html.P("Add a new country policy or strategy document to the repository. After saving, you can upload the "
                   "English and native-language PDF files.", className="text-muted-foreground text-sm mt-1"),
        ]),
        record_form.record_form("create"),
    ], className="max-w-2xl mx-auto space-y-8")


def register_callbacks(app):
    record_form.register_callbacks(app)
