"""Same-origin management panel with nonce-protected, bundled assets."""

import secrets
from importlib.resources import files

from starlette.responses import HTMLResponse

ASSETS = files("looprd").joinpath("panel_assets")
PAGE = (ASSETS.joinpath("panel.html").read_text(encoding="utf-8")
        .replace("/* PANEL_CSS */", ASSETS.joinpath("panel.css").read_text(encoding="utf-8"))
        .replace("/* PANEL_NAV */", ASSETS.joinpath("navigation.js").read_text(encoding="utf-8"))
        .replace("/* PANEL_CREDENTIALS */", ASSETS.joinpath("credentials.js").read_text(encoding="utf-8"))
        .replace("/* PANEL_FILES */", ASSETS.joinpath("files.js").read_text(encoding="utf-8"))
        .replace("/* PANEL_JS */", ASSETS.joinpath("panel.js").read_text(encoding="utf-8")))


async def panel(request):
    nonce = secrets.token_urlsafe(24)
    return HTMLResponse(PAGE.replace("NONCE", nonce), headers={
        "Cache-Control": "no-store", "Referrer-Policy": "no-referrer",
        "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": f"default-src 'none'; script-src 'nonce-{nonce}'; style-src 'nonce-{nonce}'; "
                                   "connect-src 'self'; form-action 'none'; frame-ancestors 'none'; base-uri 'none'",
    })
