"""Process-wide resources owned by the application lifespan."""

from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager

__all__ = ["LifespanResource"]


class LifespanResource[T]:
    """Holder for a resource the FastAPI lifespan opens once per process.

    The module binding stays constant while the lifespan swaps what is behind
    it, so a consumer reaches the resource through a plain accessor and no
    ``global`` rebinding is needed.  Access outside the lifespan raises, so
    misuse (a lazy first touch on a stray event loop) surfaces immediately
    instead of binding the resource to the wrong loop, and a nested entry
    raises too, so overlapping ownership shows up as a hard error rather than
    a leaked pool.

    Args:
        name: The resource as an error message names it.
        opened_by: Name of the public lifespan wrapper, quoted to a caller
            that reached the resource before it was open.
        open_resource: Builds the resource and tears it down on exit.
    """

    def __init__(
        self,
        name: str,
        opened_by: str,
        open_resource: Callable[[], AbstractAsyncContextManager[T]],
    ) -> None:
        self._name = name
        self._opened_by = opened_by
        self._open_resource = open_resource
        self._active = False
        self._resource: T | None = None

    @asynccontextmanager
    async def lifespan(self) -> AsyncIterator[None]:
        """Open the resource for the duration of the context."""
        if self._active:
            raise RuntimeError(f"{self._name} lifespan entered while already active")

        self._active = True
        try:
            async with self._open_resource() as resource:
                self._resource = resource
                yield
        finally:
            self._resource = None
            self._active = False

    def get(self) -> T:
        """Return the open resource, or raise when the lifespan is not active."""
        if self._resource is None:
            raise RuntimeError(
                f"{self._name} is not initialised. Wrap the entrypoint in "
                f"`{self._opened_by}()` (the FastAPI lifespan does this for you)."
            )

        return self._resource
