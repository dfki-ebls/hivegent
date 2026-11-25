"""The modification time of a ZIP member, exact through the extended timestamp field.

A member's DOS date names no zone and keeps two seconds at best.
The extended timestamp field of Info-ZIP carries the exact UTC second,
which zip, 7-Zip, and macOS write beside a DOS date in local time.
"""

import struct
from datetime import UTC, datetime
from zipfile import ZipInfo

__all__ = ["extended_timestamp", "member_mtime"]

_EXTENDED_TIMESTAMP = 0x5455
_HEADER = struct.Struct("<HH")
_MTIME = struct.Struct("<BI")
"""The field's flags, whose bit 0 marks the modification time that follows."""


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
