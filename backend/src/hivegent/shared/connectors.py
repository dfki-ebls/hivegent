"""The contract of a connector to an external source of documents, which plugins implement.

A connector lists and reads the files of one source and never changes them,
`sources.sync` mirrors it and `sources.webdav` holds the built-in one.
A listing the source refused in part names what it refused rather than fail,
so the sync mirrors the rest and deletes nothing it merely misses.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Protocol

import httpx2
from exar.memory.mounts import open_beneath
from exar.tools.transport import read_capped
from pydantic import BaseModel, PositiveInt

from .l10n import format_bytes

__all__ = [
    "Connector",
    "ConnectorOptions",
    "Delta",
    "RemoteItem",
]


class ConnectorOptions(BaseModel, extra="forbid"):
    """The caps every built-in connector keeps, which each extends with its own options."""

    max_depth: PositiveInt = 32
    """The deepest folder level below the root that is listed, a deeper one fails the sync."""
    max_file_size_bytes: PositiveInt = 50 * 1024 * 1024
    """The largest file that is read, a larger one fails its download."""

    def check_depth(self, path: str) -> None:
        """Raises a `ValueError` if the folder *path* lies deeper than `max_depth`."""
        if len(PurePosixPath(path).parts) > self.max_depth:
            raise ValueError(f"{path} lies deeper than max_depth {self.max_depth}")

    def _oversize(self) -> ValueError:
        return ValueError(f"The file exceeds {format_bytes(self.max_file_size_bytes)}")

    async def read_response(self, response: httpx2.Response) -> bytes:
        """The body of a streamed *response*, read until it passes `max_file_size_bytes`.

        Raises:
            ValueError: If the body exceeds `max_file_size_bytes`.
        """
        content, truncated = await read_capped(response, self.max_file_size_bytes)

        if truncated:
            raise self._oversize()

        return content

    def read_file(self, root: Path, local: str) -> bytes:
        """The content of the regular file at the slash-separated *local* path below *root*, read up to `max_file_size_bytes`.

        No symlink is followed in any segment of *local*, and anything but a regular file is refused,
        however the folder changed since it was listed (`exar.memory.mounts.open_beneath`).

        Raises:
            OSError: If the file is missing, a symlink, below one, or no regular file.
            ValueError: If the file exceeds `max_file_size_bytes`.
        """
        with open_beneath(root, local) as file:
            content = file.read(self.max_file_size_bytes + 1)

        if len(content) > self.max_file_size_bytes:
            raise self._oversize()

        return content


@dataclass(frozen=True, slots=True, kw_only=True)
class RemoteItem:
    """A file of a source.

    Args:
        id: What names the file across renames, its path if the source has nothing better.
        path: Where it lies relative to the root of the source, separated by slashes.
        version: What changes whenever its content does, such as an ETag.
        modified: When its content last changed, timezone-aware, if the source says.
    """

    id: str
    path: str
    version: str
    modified: datetime | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class Delta:
    """What changed at a source since a cursor.

    Args:
        items: The files that are new or changed, or every file if listed.
        removed: The ids or paths of the files gone since, a folder standing for every file below it,
            each deleted from the mirror at once.
        cursor: Where the next delta starts, None to list everything again.
        listed: Whether the items were meant to be every file of the source.
        refused: The paths of the files and folders the source refused to list,
            which leave a listing incomplete.
    """

    items: Sequence[RemoteItem]
    removed: Sequence[str] = ()
    cursor: str | None = None
    listed: bool = False
    refused: Sequence[str] = ()

    @property
    def complete(self) -> bool:
        """Whether the items are every file of the source, so a file missing from them is swept."""
        return self.listed and not self.refused


class Connector(Protocol):
    """The files of one source."""

    async def read(self, item: RemoteItem, /) -> bytes:
        """The content of a file."""
        ...

    async def delta(self, cursor: str | None, /) -> Delta:
        """What changed since a cursor, every file for None.

        A connector without cheaper deltas lists every file for any cursor, with `listed=True`.
        """
        ...
