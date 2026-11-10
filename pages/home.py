"""Home – port of `web/app/page.tsx`."""

from __future__ import annotations

from dash import html

from utils import queries
from utils.icons import icon
from utils.routes import href
from utils.ui import card, card_content, card_description, card_header, card_title, cn, link, link_button

TITLE = "PIM Policy Profiles AI"

MODULES = [
    dict(title="Browse curated policies",
         description="View the latest curated PIM policy and strategy documents for any country, in English or the "
                     "original language.",
         page="countries", cta="Browse countries", icon="globe", icon_bg="bg-sky-50", icon_fg="text-sky-600"),
    dict(title="Track policy changes",
         description="See what changed in a country's PIM framework — new, revised and superseded laws, regulations, "
                     "guidance and strategies — since the latest profile update or since a date you choose.",
         page="policy-changes", cta="Check changes", icon="history", icon_bg="bg-teal-50", icon_fg="text-teal-600"),
    dict(title="Contribute or update",
         description="Add new country records, upload original-language and translated source documents, and curate "
                     "metadata.",
         page="record-new", cta="Add a record", icon="upload", icon_bg="bg-emerald-50", icon_fg="text-emerald-600"),
    dict(title="AI Coach analytics",
         description="Ask country-specific questions on appraisal, transparency, PPPs, briefing, and more. Cited "
                     "answers grounded in the curated corpus.",
         page="coach", cta="Open the Coach", icon="sparkles", icon_bg="bg-violet-50", icon_fg="text-violet-600"),
    dict(title="Benchmark to peers",
         description="Compare a country to frontier good-practice and peer jurisdictions across the core PIM "
                     "dimensions.",
         page="benchmark", cta="Compare countries", icon="activity", icon_bg="bg-amber-50", icon_fg="text-amber-600"),
    dict(title="Plug-in APIs",
         description="Documented endpoints for embedding repository data, AI analytics, and benchmarks into national "
                     "ePIM-CBA deployments.",
         page="api-docs", cta="View API docs", icon="plug", icon_bg="bg-rose-50", icon_fg="text-rose-600"),
]


def _stat_tile(label: str, value: int):
    return html.Div([
        html.P(label, className="text-xs font-medium uppercase tracking-wide text-muted-foreground"),
        html.P(str(value), className="mt-1 text-3xl font-bold tabular-nums"),
    ], className="rounded-lg border bg-card p-4")


def _module_card(m: dict):
    return link(
        card(
            card_header(
                html.Div(icon(m["icon"], cn("h-5 w-5", m["icon_fg"])),
                         className=cn("flex h-10 w-10 items-center justify-center rounded-lg", m["icon_bg"])),
                card_title(m["title"], cls="mt-3 text-lg"),
                card_description(m["description"], cls="text-sm leading-relaxed"),
            ),
            card_content(
                html.Span([m["cta"], icon("arrow-right", "h-3.5 w-3.5 transition-transform group-hover:translate-x-0.5")],
                          className="inline-flex items-center gap-1 text-sm font-medium text-foreground/70 "
                                    "group-hover:text-foreground"),
            ),
            cls="h-full transition-all group-hover:-translate-y-0.5 group-hover:border-foreground/20 "
                "group-hover:shadow-md",
        ),
        href=href(m["page"]),
        cls="group block focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring "
            "focus-visible:ring-offset-2 rounded-xl",
    )


def layout(params=None):
    stats = queries.home_stats()
    grid_style = {
        "backgroundImage": "linear-gradient(rgba(2, 132, 199, 0.08) 1px, transparent 1px), "
                           "linear-gradient(90deg, rgba(2, 132, 199, 0.08) 1px, transparent 1px)",
        "backgroundSize": "32px 32px",
        "maskImage": "linear-gradient(to right, transparent, black 30%, black 100%)",
        "WebkitMaskImage": "linear-gradient(to right, transparent, black 30%, black 100%)",
    }
    return html.Div([
        # Hero
        html.Section([
            html.Div([
                html.P("PIM Policy Profiles · AI Agentic",
                       className="text-xs font-semibold uppercase tracking-wider text-sky-700"),
                html.H1([
                    "Curated Public Investment Management",
                    html.Span("policy intelligence", className="block text-sky-700"),
                ], className="mt-3 text-4xl font-bold tracking-tight sm:text-5xl"),
                html.P("A single home for country PIM policy documents, AI-powered country analytics, and "
                       "benchmarking to frontier good practice — built for ministries of finance, oversight "
                       "institutions, and ePIM-CBA deployments.",
                       className="mt-5 max-w-2xl text-base text-muted-foreground sm:text-lg"),
                html.Div([
                    link_button("Ask the AI Coach", icon("arrow-right", "ml-1 h-4 w-4"), href=href("coach"), size="lg"),
                    link_button("Global Public Investments", href=href("countries"), size="lg", variant="outline"),
                    link_button("Browse the repository", href=href("repository"), size="lg", variant="outline"),
                ], className="mt-7 flex flex-wrap gap-3"),
            ], className="max-w-3xl"),
            html.Div(className="pointer-events-none absolute inset-y-0 right-0 hidden w-1/3 opacity-30 sm:block",
                     style=grid_style, **{"aria-hidden": "true"}),
        ], className="relative overflow-hidden rounded-2xl border bg-gradient-to-br from-slate-50 via-white "
                     "to-sky-50 px-6 py-12 sm:px-12 sm:py-16"),

        # Live stats strip
        html.Section([
            _stat_tile("Countries", stats["countries"]),
            _stat_tile("Policy records", stats["records"]),
            _stat_tile("English documents", stats["eng"]),
            _stat_tile("Original-language", stats["ori"]),
        ], className="grid grid-cols-2 gap-4 sm:grid-cols-4"),

        # 5-module grid
        html.Section([
            html.Div([
                html.H2("What you can do", className="text-2xl font-semibold tracking-tight"),
                html.P("Five modules. One signed-in account.", className="hidden text-sm text-muted-foreground sm:block"),
            ], className="mb-6 flex items-baseline justify-between"),
            html.Div([_module_card(m) for m in MODULES], className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3"),
        ]),

        # What is PIM
        html.Section([
            html.H2("About this platform", className="text-xl font-semibold tracking-tight"),
            html.Div([
                html.P("Public Investment Management (PIM) shapes how governments appraise, select, budget, deliver, "
                       "and manage capital projects. This platform consolidates curated country-level PIM "
                       "legislation, regulations, strategies, and guidelines, alongside AI-assisted analytics that "
                       "cite the source documents directly."),
                html.P("All AI outputs are grounded in the curated repository — never on the open web. Source "
                       "documents are stored in their original language and, where available, in English "
                       "translation, so analyses can be reviewed against the authoritative text."),
            ], className="mt-3 grid gap-6 text-sm text-muted-foreground sm:grid-cols-2"),
        ], className="rounded-2xl border bg-muted/30 px-6 py-8 sm:px-10"),
    ], className="space-y-12")
