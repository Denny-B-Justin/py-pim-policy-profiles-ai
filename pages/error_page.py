"""Global error boundary – port of `web/app/error.tsx`."""

from dash import html

from utils.routes import href
from utils.ui import button, link

TITLE = "PIM Policy Profiles AI"


def layout(params=None):
    return html.Div([
        html.H2("Something went wrong", className="text-xl font-semibold"),
        html.P("An unexpected error occurred. Please try again or contact support if the problem persists.",
               className="text-sm text-muted-foreground max-w-md"),
        link(button("Try again", variant="outline"), href=href("home")),
    ], className="flex flex-col items-center justify-center min-h-[60vh] gap-4 text-center px-4")
