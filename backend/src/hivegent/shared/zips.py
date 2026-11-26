"""Building ZIP archives, and the modification time of a member, exact through the extended timestamp field.

A member's DOS date names no zone and keeps two seconds at best.
The extended timestamp field of Info-ZIP carries the exact UTC second,
which zip, 7-Zip, and macOS write beside a DOS date in local time.
"""

import hashlib
import struct
from collections.abc import Callable, Iterator
from contextlib import ExitStack
from datetime import UTC, datetime
from pathlib import Path
from tempfile import SpooledTemporaryFile
from zipfile import ZIP_STORED, ZipFile, ZipInfo

__all__ = [
    "build_zip",
    "extended_timestamp",
    "iter_zip",
    "member_mtime",
    "write_file",
]

_EXTENDED_TIMESTAMP = 0x5455
_HEADER = struct.Struct("<HH")
_MTIME = struct.Struct("<BI")
"""The field's flags, whose bit 0 marks the modification time that follows."""

# Archives up to this size stay in memory, larger ones roll over to disk.
_SPOOL_MEMORY = 32 * 1024 * 1024
_CHUNK_SIZE = 1024 * 1024


def member_mtime(info: ZipInfo) -> float | None:
    """When the member *info* last changed, as a POSIX timestamp.

    The extended timestamp wins, else the DOS date is read as UTC, the zone JSZip writes.
    An unset or malformed DOS date, such as the all-zero one some tools write, gives ``None``.
    """
    extra = info.extra

    while len(extra) >= _HEADER.size:
        tag, size = _HEADER.unpack_from(extra)
        field = extra[_HEADER.size : _HEADER.size + size]

        if tag == _EXTENDED_TIMESTAMP and len(field) >= _MTIME.size:
            flags, mtime = _MTIME.unpack_from(field)

            if flags & 1:
                return mtime

        extra = extra[_HEADER.size + size :]

    try:
        return datetime(*info.date_time, tzinfo=UTC).timestamp()
    except ValueError:
        return None


def extended_timestamp(mtime: float) -> bytes:
    """The extended timestamp field dating a member at *mtime*, clamped to its unsigned range."""
    second = min(max(int(mtime), 0), 2**32 - 1)

    return _HEADER.pack(_EXTENDED_TIMESTAMP, _MTIME.size) + _MTIME.pack(1, second)


def write_file(archive: ZipFile, file: Path, arcname: str) -> tuple[int, str]:
    """Copy *file* into *archive* as *arcname*, returning its size and SHA-256 digest.  Blocking.

    Its DOS date is in the server's local time, so the extended timestamp carries the exact one.
    The file is opened before the member, so a file that is gone raises
    `FileNotFoundError` with *archive* unchanged.
    """
    info = ZipInfo.from_file(file, arcname, strict_timestamps=False)
    info.compress_type = archive.compression
    info.extra = extended_timestamp(file.stat().st_mtime)
    digest = hashlib.sha256()

    with file.open("rb") as source, archive.open(info, "w") as target:
        while chunk := source.read(_CHUNK_SIZE):
            digest.update(chunk)
            target.write(chunk)

    # Writing sets the size to the bytes actually copied.
    return info.file_size, digest.hexdigest()


def build_zip(
    write: Callable[[ZipFile], None], compression: int = ZIP_STORED
) -> SpooledTemporaryFile[bytes]:
    """The ZIP archive *write* fills, built in full and rewound for reading.  Blocking."""
    with ExitStack() as cleanup:
        spool = cleanup.enter_context(SpooledTemporaryFile(max_size=_SPOOL_MEMORY))

        with ZipFile(spool, "w", compression) as archive:
            write(archive)

        spool.seek(0)
        # Built in full, so the caller owns the open archive from here.
        cleanup.pop_all()

    return spool


def iter_zip(spool: SpooledTemporaryFile[bytes]) -> Iterator[bytes]:
    """Stream a built archive, closing it once read or abandoned."""
    with spool:
        while chunk := spool.read(_CHUNK_SIZE):
            yield chunk
