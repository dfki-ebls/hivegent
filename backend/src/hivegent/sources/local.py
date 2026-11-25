"""The built-in connector, to a directory the operator mounted into the server.

The directory is whatever the operating system presents as one, an SMB or NFS share, sshfs, rclone, or a host folder.
Every delta lists every file, named by its path since inode numbers do not survive a remount of a network or FUSE file system,
and versioned by its modification time and size.
Symlinks are never followed, neither when listing nor when reading, which refuses a file that became a symlink,
or lies below one, since it was listed, and anything but a regular file or a folder is skipped or refused.
Like rclone, a folder or file the system refuses is named in the delta's `refused`,
which leaves it incomplete so nothing is swept, while an unreadable root, a root that is no mount point
where `require_mount` says it must be, so a dropped share fails the sync instead of looking empty,
and a folder deeper than `max_depth` fail the listing.
A file is read up to `max_file_size_bytes`, refused beyond it.
"""

import asyncio
import logging
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

from pydantic import AfterValidator

from ..plugins.api import SourceHost
from ..shared.connectors import Connector, ConnectorOptions, Delta, RemoteItem

__all__ = ["Local", "LocalOptions", "local"]

logger = logging.getLogger(__name__)


def _absolute(path: Path) -> Path:
    if not path.is_absolute():
        raise ValueError("The path must be absolute")

    return path


class LocalOptions(ConnectorOptions):
    """The options of a local source."""

    path: Annotated[Path, AfterValidator(_absolute)]
    """The directory that is mirrored."""
    require_mount: bool = True
    """Whether `path` must be an active mount point, so a dropped share fails the sync."""


@dataclass(frozen=True, slots=True, kw_only=True)
class Local:
    """The files below a directory."""

    options: LocalOptions

    def _walk(self) -> Delta:
        root = self.options.path

        if self.options.require_mount and not root.is_mount():
            raise ValueError(f"{root} is not a mount point")

        items: list[RemoteItem] = []
        refused: list[str] = []
        folders = [root]

        while folders:
            folder = folders.pop()

            try:
                with os.scandir(folder) as entries:
                    for entry in entries:
                        path = Path(entry.path)

                        try:
                            if entry.is_symlink():
                                continue

                            if entry.is_dir():
                                self.options.check_depth(
                                    path.relative_to(root).as_posix()
                                )
                                folders.append(path)
                            elif entry.is_file():
                                stat = entry.stat()
                                relative = path.relative_to(root).as_posix()
                                items.append(
                                    RemoteItem(
                                        id=relative,
                                        path=relative,
                                        version=f"{stat.st_mtime_ns}-{stat.st_size}",
                                        modified=datetime.fromtimestamp(
                                            stat.st_mtime, UTC
                                        ),
                                    )
                                )
                        except OSError as exc:
                            logger.warning("%s could not be listed: %s", path, exc)
                            refused.append(path.relative_to(root).as_posix())
            except OSError as exc:
                if folder == root:
                    raise

                logger.warning("%s could not be listed: %s", folder, exc)
                refused.append(folder.relative_to(root).as_posix())

        return Delta(items=items, listed=True, refused=refused)

    async def delta(self, cursor: str | None, /) -> Delta:
        """Every file, whatever the cursor.

        Raises:
            OSError: If the root cannot be listed.
            ValueError: If the root is no mount point it must be, or a folder lies deeper than `max_depth`.
        """
        return await asyncio.to_thread(self._walk)

    async def read(self, item: RemoteItem, /) -> bytes:
        """The content of a file, read up to `max_file_size_bytes`.

        Raises:
            OSError: If the file is missing, a symlink, below one, or no regular file.
            ValueError: If the file exceeds `max_file_size_bytes`.
        """
        return await asyncio.to_thread(
            self.options.read_file, self.options.path, item.path
        )


def local(host: SourceHost) -> Connector:
    """Builds the connector of a local source.

    Raises:
        ValueError: For options it refuses.
    """
    return Local(options=host.config(LocalOptions))
