"""Keep every response out of shared and browser caches."""

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

__all__ = ["NoStoreMiddleware"]


class NoStoreMiddleware:
    """Mark every HTTP response ``Cache-Control: no-store`` unless its route chose otherwise.

    The same URL resolves under whichever account calls it, a ``~/`` path to
    another workspace, so a cached response would serve one account's data to
    the next.  Without the header, a browser caches a file response
    heuristically from its ``Last-Modified``.
    """

    def __init__(self, app: ASGIApp) -> None:
        """Wrap *app*."""
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Run the wrapped app, adding the header to its response start."""
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_no_store(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers.setdefault("Cache-Control", "no-store")

            await send(message)

        await self.app(scope, receive, send_no_store)
