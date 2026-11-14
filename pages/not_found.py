"""404 – port of `web/app/not-found.tsx`."""

from dash import html

from utils.icons import icon
from utils.routes import href
from utils.ui import button, link

TITLE = "PIM Policy Profiles AI"


def layout(params=None):
    return html.Div([
        html.Div(icon("file-x", "h-8 w-8 text-muted-foreground"),
                 className="flex items-center justify-center h-16 w-16 rounded-full bg-muted"),
        html.Div([
            html.H1("Page not found", className="text-2xl font-bold"),
            html.P("The record or page you are looking for does not exist.",
                   className="text-muted-foreground mt-1 text-sm"),
        ]),
        link(button("Return to Repository"), href=href("home")),
    ], className="flex flex-col items-center justify-center min-h-[50vh] gap-6 text-center")
