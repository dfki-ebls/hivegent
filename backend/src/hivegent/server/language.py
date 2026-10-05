"""Serve every request in the language its ``Accept-Language`` header prefers."""

from starlette.datastructures import Headers
from starlette.types import ASGIApp, Receive, Scope, Send

from ..l10n import negotiate_language, use_language

__all__ = ["LanguageMiddleware"]


class LanguageMiddleware:
    """Set the ambient language for the whole request, streamed body included.

    A pure ASGI middleware rather than a dependency, so error details raised
    before routing and text produced while a response streams are localized
    alike.
    """

    def __init__(self, app: ASGIApp) -> None:
        """Wrap *app*."""
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Run the wrapped app in the request's negotiated language."""
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        language = negotiate_language(Headers(scope=scope).get("accept-language"))

        with use_language(language):
            await self.app(scope, receive, send)
