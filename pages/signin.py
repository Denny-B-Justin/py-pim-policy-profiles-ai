"""
Sign in – port of `web/app/auth/signin/page.tsx`.

NextAuth (Google + credentials) is replaced by: (1) Posit Connect single sign-on – users the platform already
authenticated are recognised automatically – and (2) local email/password accounts.  The "Continue with Google"
button is therefore not rendered; the credentials form is unchanged.
"""

from __future__ import annotations

from dash import Input, Output, State, dcc, html, no_update

from utils import auth
from utils.icons import icon
from utils.routes import href
from utils.toast import toast
from utils.ui import button, card, card_content, card_header, card_title, label, link, text_input

TITLE = "Sign in — PIM Policy Profiles AI"
_ALERT = "rounded-md bg-destructive/10 border border-destructive/20 px-3 py-2 text-sm text-destructive"


def layout(params=None):
    params = params or {}
    callback = auth.safe_callback(params.get("callbackUrl"))
    err = "Invalid credentials" if params.get("error") else ""
    user = auth.current_user()
    notice = None
    if user:
        notice = html.Div(["Signed in as ", html.Span(user.get("email") or user.get("name"), className="font-medium"),
                           " via Posit Connect." if user.get("source") == "connect" else "."],
                          className="rounded-md border border-emerald-200 bg-emerald-50 px-3 py-2 text-sm "
                                    "text-emerald-700")
    return html.Div(card(
        card_header(
            card_title("Sign in to PIM Repository", cls="text-xl"),
            html.P("Sign in to create, edit, and manage policy records", className="text-sm text-muted-foreground mt-1"),
            cls="text-center"),
        card_content(
            notice,
            html.Div(err, id="signin-error", className=_ALERT, hidden=not err),
            html.Div([
                html.Div([label("Email", html_for="signin-email"),
                          text_input("signin-email", type="email", placeholder="you@example.com", debounce=False)],
                         className="space-y-1.5"),
                html.Div([label("Password", html_for="signin-password"),
                          text_input("signin-password", type="password", debounce=False, n_submit=0)],
                         className="space-y-1.5"),
                button("Sign in with Email", id="signin-submit", type="button", cls="w-full"),
            ], className="space-y-3"),
            html.P(["Need an account? ", link("Register", href=href("register", callbackUrl=callback if callback != "?page=home" else None),
                                              cls="text-blue-700 hover:underline")],
                   className="text-center text-xs text-muted-foreground"),
            dcc.Store(id="signin-callback", data=callback),
            cls="space-y-4"),
        cls="w-full max-w-sm"), className="flex min-h-[60vh] items-center justify-center")


def register_callbacks(app):
    @app.callback(
        Output("url", "search", allow_duplicate=True), Output("signin-error", "children"),
        Output("signin-error", "hidden"), Output("toast", "data", allow_duplicate=True),
        Input("signin-submit", "n_clicks"), Input("signin-password", "n_submit"),
        State("signin-email", "value"), State("signin-password", "value"), State("signin-callback", "data"),
        prevent_initial_call=True,
        running=[(Output("signin-submit", "disabled"), True, False),
                 (Output("signin-submit", "children"), [icon("loader-circle", "h-4 w-4 mr-2 animate-spin"),
                                                         "Signing in…"], "Sign in with Email")])
    def sign_in(n, _submit, email, password, callback):
        if not (n or _submit):
            return (no_update,) * 4
        from utils.ratelimit import rate_limit  # noqa: PLC0415

        ok, retry = rate_limit(f"signin:{auth.client_ip()}", 10, 60)
        if not ok:
            return no_update, f"Too many attempts. Try again in {retry}s.", False, no_update
        user = auth.authenticate(email or "", password or "") if (email and password) else None
        if not user:
            return no_update, "Invalid email or password", False, no_update
        auth.sign_in(user)
        return auth.safe_callback(callback), "", True, toast("success", "Signed in")
