"""
shadcn/ui re-implemented as small Python helpers that emit Dash components with the
exact Tailwind class strings from the original `web/components/ui/*.tsx` files.

Because several shadcn components are *overridden* through `className` (e.g. a
`Button` with `className="h-10"`), `cn()` includes a compact `tailwind-merge`
equivalent so that later classes win just as they do in React.
"""

from __future__ import annotations

import re
from typing import Any, Iterable

from dash import dcc, html

from utils.icons import icon

# ──────────────────────────────────────────────────────────────────────────────
# cn()  =  clsx + tailwind-merge (simplified)
# ──────────────────────────────────────────────────────────────────────────────
_SIZES = r"(?:xs|sm|base|lg|xl|[2-9]xl)"
_TEXT_ALIGN = {"left", "center", "right", "justify", "start", "end", "wrap", "nowrap", "balance", "pretty",
               "ellipsis", "clip"}
_FONT_WEIGHTS = {"thin", "extralight", "light", "normal", "medium", "semibold", "bold", "extrabold", "black"}
_DISPLAY = {"block", "inline-block", "inline", "flex", "inline-flex", "grid", "inline-grid", "hidden", "table",
            "contents", "flow-root"}
_POSITION = {"static", "fixed", "absolute", "relative", "sticky"}

# (regex, group) – first match wins.  The group name is what conflicts.
_GROUPS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"^(h|w|min-h|min-w|max-h|max-w)-"), r"\1"),
    (re.compile(r"^size-"), "size"),
    (re.compile(r"^(p|px|py|pt|pr|pb|pl|ps|pe)-"), r"\1"),
    (re.compile(r"^(m|mx|my|mt|mr|mb|ml|ms|me)-"), r"\1"),
    (re.compile(r"^-(m|mx|my|mt|mr|mb|ml)-"), r"\1"),
    (re.compile(r"^gap-x-"), "gap-x"),
    (re.compile(r"^gap-y-"), "gap-y"),
    (re.compile(r"^gap-"), "gap"),
    (re.compile(r"^space-x-"), "space-x"),
    (re.compile(r"^space-y-"), "space-y"),
    (re.compile(rf"^text-{_SIZES}$"), "font-size"),
    (re.compile(r"^leading-"), "leading"),
    (re.compile(r"^tracking-"), "tracking"),
    (re.compile(r"^rounded(-(t|b|l|r|tl|tr|bl|br|s|e))?(-|$)"), None),   # handled in code
    (re.compile(r"^shadow(-|$)"), "shadow"),
    (re.compile(r"^opacity-"), "opacity"),
    (re.compile(r"^(justify|items|self|content)-"), None),                 # handled in code
    (re.compile(r"^overflow-(x-|y-)?"), None),
    (re.compile(r"^z-"), "z"),
    (re.compile(r"^(top|right|bottom|left|inset|inset-x|inset-y)-"), None),
    (re.compile(r"^grid-cols-"), "grid-cols"),
    (re.compile(r"^col-span-"), "col-span"),
    (re.compile(r"^whitespace-"), "whitespace"),
    (re.compile(r"^cursor-"), "cursor"),
    (re.compile(r"^underline-offset-"), "underline-offset"),
    (re.compile(r"^(flex-row|flex-col|flex-wrap|flex-nowrap)"), "flex-dir"),
]


def _split_variants(token: str) -> tuple[str, str]:
    """Split `md:hover:bg-red-500` -> (`md:hover:`, `bg-red-500`) ignoring ':' inside [].."""
    depth = 0
    last = -1
    for i, ch in enumerate(token):
        if ch in "[(":
            depth += 1
        elif ch in "])":
            depth -= 1
        elif ch == ":" and depth == 0:
            last = i
    return token[: last + 1], token[last + 1 :]


def _group_of(util: str) -> str | None:
    u = util.lstrip("!")
    # important modifier / opacity modifier is irrelevant for grouping
    base = u.split("/")[0] if "[" not in u else u
    if base in _DISPLAY:
        return "display"
    if base in _POSITION:
        return "position"
    if base.startswith("text-"):
        rest = base[5:]
        if re.fullmatch(_SIZES, rest) or rest.startswith("["):
            return "font-size" if re.fullmatch(_SIZES, rest) else "text-arbitrary"
        if rest in _TEXT_ALIGN:
            return "text-align"
        return "text-color"
    if base.startswith("font-"):
        rest = base[5:]
        return "font-weight" if rest in _FONT_WEIGHTS else "font-family"
    if base.startswith("bg-"):
        rest = base[3:]
        if rest.startswith(("gradient", "linear", "radial", "conic", "none")):
            return "bg-image"
        if rest in ("cover", "contain", "center", "no-repeat", "repeat"):
            return "bg-misc-" + rest
        return "bg-color"
    if base == "border" or re.fullmatch(r"border-\d+", base) or re.fullmatch(r"border-\[\d+px\]", base):
        return "border-w"
    m = re.fullmatch(r"border-(x|y|t|b|l|r|s|e)(-\d+)?", base)
    if m:
        return f"border-w-{m.group(1)}"
    if base.startswith("border-"):
        rest = base[7:]
        if rest in ("solid", "dashed", "dotted", "double", "none"):
            return "border-style"
        return "border-color"
    m = re.match(r"^rounded(?:-(t|b|l|r|tl|tr|bl|br|s|e))?(?:-|$)", base)
    if m:
        return f"rounded-{m.group(1) or ''}"
    m = re.match(r"^(justify|items|self|content)-", base)
    if m:
        return m.group(1)
    m = re.match(r"^overflow-(x-|y-)?", base)
    if m:
        return f"overflow-{(m.group(1) or '')}"
    m = re.match(r"^(top|right|bottom|left|inset|inset-x|inset-y)-", base)
    if m:
        return m.group(1)
    for rx, grp in _GROUPS:
        mm = rx.match(base)
        if mm and grp:
            return mm.expand(grp)
    return None


# Which groups does a *later* class override (beyond its own group)?
_OVERRIDES = {
    "size": {"h", "w"},
    "p": {"px", "py", "pt", "pr", "pb", "pl", "ps", "pe"},
    "px": {"pl", "pr", "ps", "pe"},
    "py": {"pt", "pb"},
    "m": {"mx", "my", "mt", "mr", "mb", "ml", "ms", "me"},
    "mx": {"ml", "mr"},
    "my": {"mt", "mb"},
    "gap": {"gap-x", "gap-y"},
    "inset": {"top", "right", "bottom", "left", "inset-x", "inset-y"},
    "inset-x": {"left", "right"},
    "inset-y": {"top", "bottom"},
    "rounded-": {"rounded-t", "rounded-b", "rounded-l", "rounded-r", "rounded-tl", "rounded-tr", "rounded-bl",
                 "rounded-br", "rounded-s", "rounded-e"},
    "border-w": {"border-w-x", "border-w-y", "border-w-t", "border-w-b", "border-w-l", "border-w-r"},
}


def _flatten(parts: Iterable[Any]) -> list[str]:
    out: list[str] = []
    for p in parts:
        if not p:
            continue
        if isinstance(p, str):
            out.extend(p.split())
        elif isinstance(p, dict):
            out.extend(k for k, v in p.items() if v)
        elif isinstance(p, (list, tuple, set)):
            out.extend(_flatten(p))
    return out


def _kids(children) -> list:
    """Flatten nested child lists – Dash renders `[[a, b]]` as an invalid React child."""
    out: list = []
    for c in children:
        if isinstance(c, (list, tuple)):
            out.extend(_kids(c))
        else:
            out.append(c)
    return out



def cn(*parts: Any) -> str:
    """Join class names, letting later conflicting Tailwind utilities win (tailwind-merge)."""
    tokens = _flatten(parts)
    keep: list[tuple[str, str | None, str]] = []   # (variants, group, token)
    for tok in tokens:
        variants, util = _split_variants(tok)
        group = _group_of(util)
        if group is not None:
            kill = {group} | _OVERRIDES.get(group, set())
            keep = [k for k in keep if not (k[0] == variants and k[1] in kill)]
        keep.append((variants, group, tok))
    seen: set[str] = set()
    out = []
    for _, _, tok in keep:
        if tok not in seen:
            seen.add(tok)
            out.append(tok)
    return " ".join(out)


# ──────────────────────────────────────────────────────────────────────────────
# Button / Badge  (class strings copied from components/ui/button.tsx & badge.tsx)
# ──────────────────────────────────────────────────────────────────────────────
_ICON_SIZE = "[&_.lucide:not([class*='size-'])]:size-4"
BUTTON_BASE = (
    "inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-md text-sm font-medium "
    "transition-all disabled:pointer-events-none disabled:opacity-50 [&_.lucide]:pointer-events-none "
    f"{_ICON_SIZE} shrink-0 [&_.lucide]:shrink-0 outline-none focus-visible:border-ring "
    "focus-visible:ring-ring/50 focus-visible:ring-[3px] aria-invalid:ring-destructive/20 "
    "aria-invalid:border-destructive cursor-pointer"
)
BUTTON_VARIANTS = {
    "default": "bg-primary text-primary-foreground hover:bg-primary/90",
    "destructive": "bg-destructive text-white hover:bg-destructive/90 focus-visible:ring-destructive/20",
    "outline": "border bg-background shadow-xs hover:bg-accent hover:text-accent-foreground",
    "secondary": "bg-secondary text-secondary-foreground hover:bg-secondary/80",
    "ghost": "hover:bg-accent hover:text-accent-foreground",
    "link": "text-primary underline-offset-4 hover:underline",
}
BUTTON_SIZES = {
    "default": "h-9 px-4 py-2 has-[>.lucide]:px-3",
    "xs": "h-6 gap-1 rounded-md px-2 text-xs has-[>.lucide]:px-1.5 [&_.lucide:not([class*='size-'])]:size-3",
    "sm": "h-8 rounded-md gap-1.5 px-3 has-[>.lucide]:px-2.5",
    "lg": "h-10 rounded-md px-6 has-[>.lucide]:px-4",
    "icon": "size-9",
    "icon-xs": "size-6 rounded-md [&_.lucide:not([class*='size-'])]:size-3",
    "icon-sm": "size-8",
    "icon-lg": "size-10",
}


def button_class(variant: str = "default", size: str = "default", cls: str = "") -> str:
    return cn(BUTTON_BASE, BUTTON_VARIANTS[variant], BUTTON_SIZES[size], cls)


def button(*children, variant: str = "default", size: str = "default", cls: str = "", **props):
    """<Button>.  Pass `id=`, `n_clicks=`, `disabled=` … straight through to html.Button."""
    props.setdefault("type", "button")
    return html.Button(_kids(children), className=button_class(variant, size, cls), **props)


def link_button(*children, href: str, variant: str = "default", size: str = "default", cls: str = "",
                external: bool = False, **props):
    """<Button asChild><Link/></Button>  → an anchor styled as a button."""
    klass = button_class(variant, size, cls)
    if external:
        props.setdefault("target", "_blank")
        props.setdefault("rel", "noopener noreferrer")
        return html.A(_kids(children), href=href, className=klass, **props)
    return link(*children, href=href, cls=klass, **props)


def link(*children, href: str, cls: str = "", **props):
    """Client-side navigation link (no page reload).  Use html.A for external URLs / downloads."""
    return dcc.Link(_kids(children), href=href, className=cls, refresh=False, **props)


BADGE_BASE = (
    "inline-flex items-center justify-center rounded-full border border-transparent px-2 py-0.5 text-xs "
    "font-medium w-fit whitespace-nowrap shrink-0 [&>.lucide]:size-3 gap-1 [&>.lucide]:pointer-events-none "
    "transition-[color,box-shadow] overflow-hidden"
)
BADGE_VARIANTS = {
    "default": "bg-primary text-primary-foreground",
    "secondary": "bg-secondary text-secondary-foreground",
    "destructive": "bg-destructive text-white",
    "outline": "border-border text-foreground",
    "ghost": "",
    "link": "text-primary underline-offset-4",
}


def badge(*children, variant: str = "default", cls: str = "", **props):
    return html.Span(_kids(children), className=cn(BADGE_BASE, BADGE_VARIANTS[variant], cls), **props)


# ──────────────────────────────────────────────────────────────────────────────
# Card
# ──────────────────────────────────────────────────────────────────────────────
def card(*children, cls: str = "", **props):
    return html.Div(_kids(children), className=cn(
        "bg-card text-card-foreground flex flex-col gap-6 rounded-xl border py-6 shadow-sm", cls), **props)


def card_header(*children, cls: str = "", **props):
    return html.Div(_kids(children), className=cn(
        "@container/card-header grid auto-rows-min grid-rows-[auto_auto] items-start gap-2 px-6 "
        "has-data-[slot=card-action]:grid-cols-[1fr_auto] [.border-b]:pb-6", cls), **props)


def card_title(*children, cls: str = "", **props):
    return html.Div(_kids(children), className=cn("leading-none font-semibold", cls), **props)


def card_description(*children, cls: str = "", **props):
    return html.Div(_kids(children), className=cn("text-muted-foreground text-sm", cls), **props)


def card_content(*children, cls: str = "", **props):
    return html.Div(_kids(children), className=cn("px-6", cls), **props)


def card_footer(*children, cls: str = "", **props):
    return html.Div(_kids(children), className=cn("flex items-center px-6 [.border-t]:pt-6", cls), **props)


# ──────────────────────────────────────────────────────────────────────────────
# Misc primitives
# ──────────────────────────────────────────────────────────────────────────────
def separator(orientation: str = "horizontal", cls: str = "", **props):
    return html.Div(
        className=cn("bg-border shrink-0 data-[orientation=horizontal]:h-px data-[orientation=horizontal]:w-full "
                     "data-[orientation=vertical]:h-full data-[orientation=vertical]:w-px", cls),
        **{"data-orientation": orientation, "role": "none"}, **props,
    )


def label(*children, cls: str = "", html_for: str | None = None, **props):
    kw = {"htmlFor": html_for} if html_for else {}
    return html.Label(_kids(children), className=cn(
        "flex items-center gap-2 text-sm leading-none font-medium select-none", cls), **kw, **props)


INPUT_CLASS = (
    "file:text-foreground placeholder:text-muted-foreground selection:bg-primary selection:text-primary-foreground "
    "border-input h-9 w-full min-w-0 rounded-md border bg-transparent px-3 py-1 text-base shadow-xs "
    "transition-[color,box-shadow] outline-none file:inline-flex file:h-7 file:border-0 file:bg-transparent "
    "file:text-sm file:font-medium disabled:pointer-events-none disabled:cursor-not-allowed disabled:opacity-50 "
    "md:text-sm focus-visible:border-ring focus-visible:ring-ring/50 focus-visible:ring-[3px] "
    "aria-invalid:ring-destructive/20 aria-invalid:border-destructive"
)
TEXTAREA_CLASS = (
    "border-input placeholder:text-muted-foreground focus-visible:border-ring focus-visible:ring-ring/50 "
    "aria-invalid:ring-destructive/20 aria-invalid:border-destructive flex min-h-16 w-full rounded-md border "
    "bg-transparent px-3 py-2 text-base shadow-xs transition-[color,box-shadow] outline-none "
    "focus-visible:ring-[3px] disabled:cursor-not-allowed disabled:opacity-50 md:text-sm"
)


def text_input(id=None, cls: str = "", type: str = "text", **props):
    """<Input/> → dcc.Input (debounced by default so callbacks don't fire on every keystroke)."""
    if id is not None:
        props["id"] = id
    props.setdefault("debounce", True)
    props.setdefault("autoComplete", "off")
    return dcc.Input(type=type, className=cn(INPUT_CLASS, cls), **props)


def textarea(id=None, cls: str = "", **props):
    if id is not None:
        props["id"] = id
    return dcc.Textarea(className=cn(TEXTAREA_CLASS, cls), **props)


def skeleton(cls: str = ""):
    return html.Div(className=cn("bg-accent animate-pulse rounded-md", cls))


def spinner(cls: str = "h-4 w-4"):
    return icon("loader-circle", cn("animate-spin", cls))


def loading_spinner(cls: str = "h-4 w-4"):
    """components/rag/LoadingSpinner.tsx (ring + arc), used by the briefing panel buttons."""
    return html.Span(className=cn("loading-spinner animate-spin", cls), **{"aria-hidden": "true"})


def select(id, options, value=None, placeholder: str = "Select…", cls: str = "", clearable: bool = False,
           searchable: bool = False, multi: bool = False, size: str = "default", disabled: bool = False,
           menu_width: str | None = None, **props):
    """shadcn <Select> trigger look on top of dcc.Dropdown.  options = [(value,label)] or dicts.

    `menu_width` (e.g. "20rem") widens the open menu beyond the trigger, like SelectContent growing to its content.
    """
    opts = [o if isinstance(o, dict) else {"label": o[1], "value": o[0]} for o in options]
    props.setdefault("optionHeight", 32)     # shadcn SelectItem: py-1.5 + text-sm line-height
    props.setdefault("maxHeight", 320)
    if menu_width:
        props["style"] = {**props.get("style", {}), "--ui-select-menu-w": menu_width}
    return dcc.Dropdown(
        id=id, options=opts, value=value, placeholder=placeholder, clearable=clearable, searchable=searchable,
        multi=multi, disabled=disabled, className=cn("ui-select", f"ui-select-{size}", cls), **props,
    )


# ──────────────────────────────────────────────────────────────────────────────
# Combobox (shadcn Popover + Command, e.g. components/records/CountryCombobox.tsx).
# Rendered server-side; open/close, filtering and selection are plain DOM handlers in assets/app.js
# (`[data-cb]`).  The selected value lives in a dcc.Store with id `id` (read it with State(id, "data")).
# ──────────────────────────────────────────────────────────────────────────────
def combobox(id: str, options, value=None, placeholder: str = "Select…", search_placeholder: str = "Search…",
             empty_text: str = "No results found.", clear_label: str = "— None —", cls: str = "",
             popover_cls: str = "w-[340px]"):
    """options = [(value, label, badge_text)]  → trigger button + searchable popover list."""
    sel = next((o for o in options if o[0] == value), None)

    def chip(text):
        return html.Span(text, className="text-xs font-mono text-muted-foreground bg-muted px-1.5 py-0.5 rounded shrink-0")

    def check(on: bool):
        return icon("check", cn("cb-check mr-2 h-4 w-4 shrink-0", "opacity-100" if on else "opacity-0"))

    item_cls = ("cb-item relative flex cursor-default items-center gap-2 rounded-sm px-2 py-1.5 text-sm outline-hidden "
                "select-none data-[active=true]:bg-accent data-[active=true]:text-accent-foreground")
    items = [html.Div([check(not value), "— None —" if clear_label is None else clear_label],
                      className=cn(item_cls, "text-muted-foreground"), role="option",
                      **{"data-cb-item": "", "data-value": "", "data-search": "__clear__"})]
    for v, lab, bdg in options:
        items.append(html.Div([check(v == value), html.Span([chip(bdg), lab] if bdg else [lab],
                                                          className="flex items-center gap-2 w-full")],
                              className=item_cls, role="option",
                              **{"data-cb-item": "", "data-value": v, "data-label": lab, "data-badge": bdg or "",
                                 "data-search": f"{lab} {bdg or ''} {v}".lower()}))
    trigger_inner = (html.Span([chip(sel[2]), sel[1]] if sel[2] else [sel[1]], className="flex items-center gap-2")
                     if sel else html.Span(placeholder, className="text-muted-foreground"))
    return html.Div([
        dcc.Store(id=id, data=value),
        html.Button([html.Span(trigger_inner, className="cb-label min-w-0 truncate"),
                     icon("chevrons-up-down", "ml-2 h-4 w-4 shrink-0 opacity-50")],
                    type="button", role="combobox", className=button_class("outline", "default",
                                                                          "w-full justify-between font-normal h-9"),
                    **{"data-cb-trigger": "", "aria-expanded": "false", "data-placeholder": placeholder}),
        html.Div([
            html.Div([icon("search", "size-4 shrink-0 opacity-50"),
                      dcc.Input(type="text", placeholder=search_placeholder, autoComplete="off", debounce=False,
                                className="cb-search placeholder:text-muted-foreground flex h-9 w-full rounded-md "
                                          "bg-transparent py-3 text-sm outline-hidden")],
                     className="flex h-9 items-center gap-2 border-b px-3"),
            html.Div([html.Div(items, className="p-1 overflow-hidden", **{"data-cb-list": ""}),
                      html.Div(empty_text, className="py-6 text-center text-sm hidden", **{"data-cb-empty": ""})],
                     className="max-h-[300px] overflow-x-hidden overflow-y-auto scroll-py-1"),
        ], className=cn("bg-popover text-popover-foreground absolute left-0 top-full z-50 mt-1 rounded-md border p-0 "
                        "shadow-md outline-hidden", popover_cls), style={"display": "none"}, **{"data-cb-pop": ""}),
    ], className=cn("relative", cls), **{"data-cb": id})


# ──────────────────────────────────────────────────────────────────────────────
# Table (components/ui/table.tsx)
# ──────────────────────────────────────────────────────────────────────────────
def table(*children, cls: str = "", container_cls: str = "", **props):
    return html.Div(
        html.Table(_kids(children), className=cn("w-full caption-bottom text-sm", cls), **props),
        className=cn("relative w-full overflow-x-auto", container_cls),
    )


def thead(*children, cls: str = "", **props):
    return html.Thead(_kids(children), className=cn("[&_tr]:border-b", cls), **props)


def tbody(*children, cls: str = "", **props):
    return html.Tbody(_kids(children), className=cn("[&_tr:last-child]:border-0", cls), **props)


def tr(*children, cls: str = "", **props):
    return html.Tr(_kids(children), className=cn("hover:bg-muted/50 border-b transition-colors", cls), **props)


def th(*children, cls: str = "", **props):
    return html.Th(_kids(children), className=cn(
        "text-foreground h-10 px-2 text-left align-middle font-medium whitespace-nowrap", cls), **props)


def td(*children, cls: str = "", **props):
    return html.Td(_kids(children), className=cn("p-2 align-middle whitespace-nowrap", cls), **props)


# ──────────────────────────────────────────────────────────────────────────────
# Tabs (components/ui/tabs.tsx) – pure-CSS/JS tabs driven by data-state attributes
# ──────────────────────────────────────────────────────────────────────────────
TABS_LIST = ("rounded-lg p-[3px] h-9 text-muted-foreground inline-flex w-fit items-center justify-center bg-muted "
             "group/tabs-list")
TABS_TRIGGER = (
    "text-foreground/60 hover:text-foreground relative inline-flex h-[calc(100%-1px)] flex-1 items-center "
    "justify-center gap-1.5 rounded-md border border-transparent px-2 py-1 text-sm font-medium whitespace-nowrap "
    "transition-all focus-visible:ring-[3px] focus-visible:outline-1 disabled:pointer-events-none "
    "disabled:opacity-50 [&_.lucide]:pointer-events-none [&_.lucide]:shrink-0 "
    "[&_.lucide:not([class*='size-'])]:size-4 data-[state=active]:bg-background data-[state=active]:text-foreground "
    "data-[state=active]:shadow-sm cursor-pointer"
)


def tabs_list(*children, cls: str = "", **props):
    return html.Div(_kids(children), className=cn(TABS_LIST, cls), role="tablist", **props)


def tabs_trigger(*children, active: bool = False, cls: str = "", **props):
    return html.Button(_kids(children), className=cn(TABS_TRIGGER, cls), type="button", role="tab",
                       **{"data-state": "active" if active else "inactive"}, **props)


# ──────────────────────────────────────────────────────────────────────────────
# Small layout helpers shared by pages
# ──────────────────────────────────────────────────────────────────────────────
def page_heading(title: str, subtitle: str | None = None, *, cls: str = "", right=None):
    head = html.Div([
        html.H1(title, className="text-2xl font-bold tracking-tight"),
        html.P(subtitle, className="text-sm text-muted-foreground mt-1") if subtitle else None,
    ])
    if right is None:
        return html.Div(head, className=cls)
    return html.Div([head, right], className=cn("flex items-start justify-between gap-4", cls))


def alert_box(message, kind: str = "error", cls: str = ""):
    palette = {
        "error": "border-red-200 bg-red-50 text-red-700",
        "warn": "border-amber-200 bg-amber-50 text-amber-800",
        "info": "border-blue-200 bg-blue-50 text-blue-700",
        "success": "border-emerald-200 bg-emerald-50 text-emerald-700",
    }[kind]
    return html.Div(message, className=cn("rounded-md border px-3 py-2 text-sm", palette, cls))


def empty_state(message: str, cls: str = ""):
    return html.Div(message, className=cn("text-center py-12 text-sm text-muted-foreground", cls))


# ──────────────────────────────────────────────────────────────────────────────
# Dialog (components/ui/dialog.tsx) – server-rendered overlay shown/hidden by a callback that
# returns DIALOG_SHOW / DIALOG_HIDE for `Output(<id>, "style")`.
# ──────────────────────────────────────────────────────────────────────────────
DIALOG_SHOW = {"display": "block"}
DIALOG_HIDE = {"display": "none"}


def dialog(id: str, *children, open: bool = False, cls: str = "", show_close: bool = True):
    """<Dialog><DialogContent>…  Overlay id = `<id>-overlay`, close (X) button id = `<id>-x`."""
    content = html.Div(
        [*children,
         html.Button([icon("x", "size-4"), html.Span("Close", className="sr-only")], id=f"{id}-x", type="button",
                     className="ring-offset-background focus:ring-ring absolute top-4 right-4 rounded-xs opacity-70 "
                                "transition-opacity hover:opacity-100 focus:ring-2 focus:ring-offset-2 "
                                "focus:outline-hidden cursor-pointer") if show_close else None],
        className=cn("bg-background animate-in fade-in-0 zoom-in-95 fixed top-[50%] left-[50%] z-50 grid w-full "
                     "max-w-[calc(100%-2rem)] translate-x-[-50%] translate-y-[-50%] gap-4 rounded-lg border p-6 "
                     "shadow-lg duration-200 outline-none sm:max-w-lg", cls),
        role="dialog", **{"aria-modal": "true"})
    return html.Div([
        html.Div(id=f"{id}-overlay", className="animate-in fade-in-0 fixed inset-0 z-50 bg-black/50"),
        content,
    ], id=id, style=DIALOG_SHOW if open else DIALOG_HIDE)


def dialog_header(*children, cls: str = ""):
    return html.Div(_kids(children), className=cn("flex flex-col gap-2 text-center sm:text-left", cls))


def dialog_footer(*children, cls: str = ""):
    return html.Div(_kids(children), className=cn("flex flex-col-reverse gap-2 sm:flex-row sm:justify-end", cls))


def dialog_title(*children, cls: str = "", **props):
    return html.H2(_kids(children), className=cn("text-lg leading-none font-semibold", cls), **props)


def dialog_description(*children, cls: str = "", **props):
    return html.Div(_kids(children), className=cn("text-muted-foreground text-sm", cls), **props)
