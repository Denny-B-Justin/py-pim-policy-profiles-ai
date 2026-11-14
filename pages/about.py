"""About – port of `web/app/about/page.tsx`."""

from __future__ import annotations

from dash import html

from utils.ui import badge, cn, separator

TITLE = "About · PIM Policy Profiles AI"

POLICY_TIERS = [
    dict(code="Tier 1", label="Primary Legislation", tagline='the "why" and "what"',
         badge="bg-blue-50 text-blue-700 border-blue-200", border="border-l-blue-400",
         body="The foundational legal authority for PIM, typically established through an Organic Budget Law, Public "
              "Finance Management Law, or a dedicated Public Investment Law. This tier sets the overall objectives "
              "and principles of public investment, establishes which entities are subject to the rules, and "
              "defines key concepts. It should be durable and not change frequently. In most countries, this is "
              "where the mandate for PIM is anchored — without this, the framework lacks legal force."),
    dict(code="Tier 2", label="Secondary Regulations / Decrees", tagline='the "who"',
         badge="bg-indigo-50 text-indigo-700 border-indigo-200", border="border-l-indigo-400",
         body="Regulations, decrees, or statutory instruments issued under the authority of Tier 1. They specify "
              "roles and responsibilities of different institutions (e.g., the Ministry of Finance, sector "
              "ministries, line agencies), set the rules of the process in more detail, and establish "
              "accountability and compliance requirements. They are more specific than primary legislation but "
              "still relatively high-level."),
    dict(code="Tier 3", label="Procedural Guidelines and Methodological Guidance", tagline='the "how"',
         badge="bg-violet-50 text-violet-700 border-violet-200", border="border-l-violet-400",
         body="Operational manuals, circulars, and technical guidelines that provide the practical step-by-step "
              "instructions for how to carry out appraisal, selection, implementation and evaluation of projects. "
              "This is where, for example, cost-benefit analysis methodology and project appraisal templates live. "
              "These can and should be updated relatively frequently as practice evolves."),
    dict(code="Tier 4", label="Strategies for Project Prioritization & Alignment", tagline=None,
         badge="bg-teal-50 text-teal-700 border-teal-200", border="border-l-teal-400", body=None),
]

STRATEGY_TYPES = [
    (1, "National (Longer Term, 5 Years+)"), (2, "Medium Term (3–5 years)"), (3, "Sectoral"),
    (4, "Cross-Cutting (Climate)"), (5, "Cross-Cutting (Other)"), (6, "Sub-National Strategy"),
]

REFERENCES = [
    dict(citation="Kim, J.-H., Fallov, J. A., & Groom, S. (2020). Public Investment Management Reference Guide. "
                  "Washington, DC: World Bank International Development in Practice, pp. 279.",
         url="https://openknowledge.worldbank.org/server/api/core/bitstreams/2e0fa0e5-254e-5119-ab70-4ebfebc3f8ef/content",
         link_label="Open Knowledge Repository"),
    dict(citation="Manescu, C. B. (2024). The Planning of Public Investments in EU Member States: Long-Term Strategy, "
                  "Selection & Budgeting Issues. Brussels: European Commission Directorate-General for Economic and "
                  "Financial Affairs, European Economy Discussion Papers, 213, December, pp. 36.",
         url="https://economy-finance.ec.europa.eu/publications/planning-public-investments-eu-member-states-long-term-strategy-selection-and-budgeting-issues_en",
         link_label="European Commission Publications"),
]


def _tier_card(t: dict):
    children = [
        html.Div([
            badge(t["code"], variant="outline", cls=cn("text-xs font-semibold px-2.5 py-0.5", t["badge"])),
            html.Span(t["label"], className="font-semibold text-sm"),
            html.Span(f"— {t['tagline']}", className="text-xs text-muted-foreground italic") if t["tagline"] else None,
        ], className="flex items-center gap-2.5 flex-wrap"),
    ]
    if t["body"]:
        children.append(html.P(t["body"], className="text-sm text-muted-foreground leading-relaxed"))
    if t["code"] == "Tier 4":
        children.append(html.Div([
            html.P("Strategy documents classified under Tier 4 are further categorized by the following strategy types:",
                   className="text-sm text-muted-foreground leading-relaxed"),
            html.Ol([
                html.Li([
                    html.Span(str(n), className="flex h-5 w-5 items-center justify-center rounded-full bg-teal-100 "
                                                "text-teal-700 text-[11px] font-semibold shrink-0"),
                    label,
                ], className="flex items-center gap-3 text-sm text-muted-foreground")
                for n, label in STRATEGY_TYPES
            ], className="space-y-1.5 mt-2"),
        ], className="mt-3 space-y-2"))
    return html.Div(children, className=cn("rounded-xl border bg-muted/20 border-l-4 p-5 space-y-2", t["border"]))


def layout(params=None):
    return html.Div([
        html.Div([
            html.H1("About", className="text-3xl font-bold tracking-tight"),
            html.Div([
                html.P([
                    "The ",
                    html.A("pim-pam.net", href="https://pim-pam.net", target="_blank", rel="noopener noreferrer",
                           className="font-semibold text-foreground underline underline-offset-2 "
                                     "hover:text-blue-600 transition-colors"),
                    " Global Public Investment Management (PIM) Policies AI Repository provides a systematic online "
                    "resource for the comparative review of ", html.Em("de jure"),
                    " institutional contexts, as well as online decision support for core and climate-informed "
                    "project preparation, resourcing, and delivery. The PIM Policies repository is a dynamic "
                    "resource designed to capture all relevant national and, as pertinent, sub-national PIM policy "
                    "guidelines.",
                ]),
                html.P("The PIM Country Policy Repositories serve to (i) provide interactive AI-powered guidance to "
                       "key PIM process participants (especially project proponents at national, sub-national, and "
                       "state-owned levels), and (ii) provide a PIM policy comprehensiveness profile (benchmarked "
                       "against global good practices)."),
                html.P([
                    "The policy documents are organized within a three-tier hierarchy of policy guidance that a good "
                    "PIM framework should have, as identified in Kim et al. (2020), ",
                    html.Em("PIM Reference Guide Chapter 3"),
                    ", specifically the InfraGov 2.0 Dimensions 9 Institutional Design and 1 (PIM Project "
                    "Guidance). A fourth category is dedicated to strategies considered for project prioritization "
                    "and alignment, organized into national (long versus medium term), sectoral, cross-cutting "
                    "(e.g., climate), cross-cutting (e.g., other), and sub-national categories.",
                ]),
            ], className="space-y-3 text-muted-foreground leading-relaxed"),
        ], className="space-y-4"),

        separator(),

        html.Section([
            html.Div([
                html.H2("PIM Policy Document Classifications", className="text-xl font-semibold tracking-tight"),
                html.P("A three-tier hierarchy plus a strategic alignment category.",
                       className="text-sm text-muted-foreground"),
            ], className="space-y-1"),
            html.Div([_tier_card(t) for t in POLICY_TIERS], className="space-y-4"),
        ], className="space-y-6"),

        separator(),

        html.Section([
            html.H2("Selected References", className="text-xl font-semibold tracking-tight"),
            html.Ul([
                html.Li([
                    html.Span(className="mt-1 h-2 w-2 rounded-full bg-muted-foreground/40 shrink-0"),
                    html.Div([
                        html.P(r["citation"], className="text-sm text-muted-foreground leading-relaxed"),
                        html.A(f"{r['link_label']} ↗", href=r["url"], target="_blank", rel="noopener noreferrer",
                               className="inline-flex items-center gap-1 text-xs text-blue-600 hover:text-blue-800 "
                                         "hover:underline transition-colors"),
                    ], className="space-y-1"),
                ], className="flex gap-4")
                for r in REFERENCES
            ], className="space-y-5"),
        ], className="space-y-5"),
    ], className="max-w-3xl mx-auto space-y-12")
