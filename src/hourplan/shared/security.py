"""Security headers applied to every response."""

from flask import Flask, Response

CONTENT_SECURITY_POLICY = "; ".join(
    [
        "default-src 'self'",
        "script-src 'self'",
        "style-src 'self'",
        "img-src 'self' data:",
        "font-src 'self'",
        "connect-src 'self'",
        "object-src 'none'",
        "base-uri 'self'",
        "form-action 'self'",
        "frame-ancestors 'none'",
    ]
)

STATIC_HEADERS = {
    "Content-Security-Policy": CONTENT_SECURITY_POLICY,
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=()",
    "Cross-Origin-Opener-Policy": "same-origin",
}

HSTS = "max-age=31536000; includeSubDomains"


def init_security_headers(app: Flask) -> None:
    send_hsts = bool(app.config.get("SECURITY_HSTS", True))
    headers = dict(STATIC_HEADERS)
    if app.config.get("VOICE_STT_URL"):
        headers["Permissions-Policy"] = headers["Permissions-Policy"].replace(
            "microphone=()", "microphone=(self)"
        )

    @app.after_request
    def _set_headers(response: Response) -> Response:
        for name, value in headers.items():
            response.headers.setdefault(name, value)
        if send_hsts:
            response.headers.setdefault("Strict-Transport-Security", HSTS)
        return response
