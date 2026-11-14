"""
Lucide icons.

The original app uses `lucide-react` inline SVGs.  Dash has no `html.Svg`, so every
icon is rendered as a `<span class="lucide lucide-<name>">` whose glyph is a CSS mask
(see tools/build_icons.py -> assets/style.css).  The mask uses `currentColor`, the same
24x24 geometry and 2px stroke, so the result is pixel-identical to the React icons and
can be sized/coloured with the same Tailwind classes (`h-4 w-4`, `text-muted-foreground`,
`animate-spin` ...).
"""

from __future__ import annotations

from dash import html


def icon(name: str, cls: str = "", **props):
    """Render a lucide icon. `name` is the kebab-case lucide name, e.g. "arrow-left"."""
    cls_name = f"lucide lucide-{name}" + (f" {cls}" if cls else "")
    return html.Span(className=cls_name, **props)
