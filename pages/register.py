"""Register – port of `web/app/auth/register/page.tsx` (POST /api/auth/register + auto sign-in)."""

from __future__ import annotations

from dash import Input, Output, State, dcc, html, no_update

from pages.pages_constants import REGISTER_TITLE as TITLE
from utils import auth
from utils.icons import icon
from utils.routes import href
from utils.toast import toast
from utils.ui import button, card, card_content, card_header, card_title, label, link, text_input

_ALERT = "rounded-md border border-destructive/20 bg-destructive/10 px-3 py-2 text-sm text-destructive"


def layout(params=None):
    callback = auth.safe_callback((params or {}).get("callbackUrl"))
    return html.Div(card(
        card_header(
            card_title("Create an account", cls="text-xl"),
            html.P("Required to use translation and other authenticated actions", className="mt-1 text-sm text-muted-foreground"),
            cls="text-center"),
        card_content(
            html.Div("", id="register-error", className=_ALERT, hidden=True),
            html.Div([
                html.Div([label("Name", html_for="register-name"),
                          text_input("register-name", placeholder="Your name", debounce=False)], className="space-y-1.5"),
                html.Div([label("Email", html_for="register-email"),
                          text_input("register-email", type="email", placeholder="you@example.com", debounce=False)],
                         className="space-y-1.5"),
                html.Div([label("Password", html_for="register-password"),
                          text_input("register-password", type="password", placeholder="At least 8 characters",
                                     minLength=8, debounce=False)], className="space-y-1.5"),
                html.Div([label(["Invite code ", html.Span("(if you have one)", className="font-normal text-muted-foreground")],
                                html_for="register-invite"),
                          text_input("register-invite", placeholder="Leave blank for the first account", debounce=False,
                                     n_submit=0)], className="space-y-1.5"),
                button("Create account", id="register-submit", type="button", cls="w-full"),
            ], className="space-y-3"),
            html.P(["Already have an account? ", link("Sign in", href=href("signin", callbackUrl=callback if callback != "?page=home" else None),
                                                      cls="text-blue-700 hover:underline")],
                   className="text-center text-xs text-muted-foreground"),
            dcc.Store(id="register-callback", data=callback),
            cls="space-y-4"),
        cls="w-full max-w-sm"), className="flex min-h-[60vh] items-center justify-center")


def register_callbacks(app):
    @app.callback(
        Output("url", "search", allow_duplicate=True), Output("register-error", "children"),
        Output("register-error", "hidden"), Output("toast", "data", allow_duplicate=True),
        Input("register-submit", "n_clicks"), Input("register-invite", "n_submit"),
        State("register-name", "value"), State("register-email", "value"), State("register-password", "value"),
        State("register-invite", "value"), State("register-callback", "data"), prevent_initial_call=True,
        running=[(Output("register-submit", "disabled"), True, False),
                 (Output("register-submit", "children"), [icon("loader-circle", "mr-2 h-4 w-4 animate-spin"),
                                                           "Creating account…"], "Create account")])
    def create(n, _submit, name, email, password, invite, callback):
        if not (n or _submit):
            return (no_update,) * 4
        from utils.ratelimit import rate_limit  # noqa: PLC0415

        ok, retry = rate_limit(f"register:{auth.client_ip()}", 10, 60)
        if not ok:
            return no_update, f"Too many attempts. Try again in {retry}s.", False, no_update
        ok, err = auth.register(name, email or "", password or "", invite or None)
        if not ok:
            return no_update, err or "Registration failed", False, no_update
        user = auth.authenticate(email, password)  # auto sign-in with the new credentials
        if not user:
            return href("signin", callbackUrl=callback), "", True, no_update
        auth.sign_in(user)
        return auth.safe_callback(callback), "", True, toast("success", "Account created")
