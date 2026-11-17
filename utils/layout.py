"""
Application shell – port of `web/app/layout.tsx` (sticky header, <main>, footer, toaster).
"""

from __future__ import annotations

from dash import dcc, html

import constants as C
from utils.icons import icon
from utils.routes import asset, href
from utils.ui import cn, link, separator

# (page, label, short_label, icon) — identical order to PRIMARY_NAV / SECONDARY_NAV in layout.tsx
PRIMARY_NAV = [
    ("countries", "Country Public Investments", "Investments", "globe"),
    ("map", "Map", None, "map"),
    ("repository", "Repository", None, "book-open"),
    ("coach", "Coach", None, "sparkles"),
    ("benchmark", "Benchmark", None, "activity"),
    ("api-docs", "API", None, "plug"),
]
SECONDARY_NAV = [
    ("about", "About", None, "info"),
    ("release-notes", "Release notes", None, "scroll-text"),
]

_NAV_LINK = ("flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground px-3 py-1.5 "
             "rounded-md hover:bg-muted transition-colors")


def _nav_item(page: str, label: str, short: str | None, ico: str):
    if short:
        text = [html.Span(short, className="xl:hidden"), html.Span(label, className="hidden xl:inline")]
    else:
        text = [label]
    return link(icon(ico, "h-3.5 w-3.5"), *text, href=href(page), cls=_NAV_LINK)


def header():
    return html.Header(
        html.Div(
            html.Div([
                link(
                    html.Img(src=asset("pim-pam-logo.png"), alt=C.APP_NAME, height="40", width="120",
                             className="h-10 w-auto"),
                    href=href("home"), cls="flex items-center leading-none",
                ),
                html.Nav([
                    *[_nav_item(*n) for n in PRIMARY_NAV],
                    separator("vertical", "h-5 mx-1"),
                    *[_nav_item(*n) for n in SECONDARY_NAV],
                ], className="hidden lg:flex items-center gap-1"),
                # Below lg: condensed nav – just the primary modules as icons
                html.Nav([
                    link(icon(ico, "h-4 w-4"), href=href(page), title=label,
                         cls="flex h-9 w-9 items-center justify-center rounded-md text-muted-foreground "
                             "hover:bg-muted hover:text-foreground")
                    for page, label, _s, ico in PRIMARY_NAV
                ], className="flex items-center gap-1 lg:hidden"),
            ], className="flex h-14 items-center justify-between gap-4"),
            className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8",
        ),
        className="sticky top-0 z-50 border-b bg-white/95 backdrop-blur supports-[backdrop-filter]:bg-white/60",
    )


def footer():
    return html.Footer(C.FOOTER_TEXT, className="border-t mt-16 py-6 text-center text-xs text-muted-foreground")


def shell():
    """The static application frame.  The routed page renders into `#page-content`."""
    return html.Div([
        dcc.Location(id="url", refresh="callback-nav"),
        dcc.Store(id="toast"),                       # {type, message, description?, t}
        dcc.Store(id="nav-title"),                   # document.title written by the router
        dcc.Store(id="page-refresh"),                # any callback may write a timestamp here to re-render the page
        html.Div(id="nav-noop", hidden=True),        # dummy outputs of the clientside callbacks
        html.Div(id="toast-noop", hidden=True),
        header(),
        html.Main(id="page-content", className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8"),
        footer(),
        html.Div(id="toaster", className="pim-toaster", **{"aria-live": "polite"}),
    ], className="min-h-screen bg-background")
