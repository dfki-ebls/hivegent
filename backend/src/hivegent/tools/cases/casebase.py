"""Opening and caching the case bases that a query ranks, and binding the host's models.

Every file is reached through the search paths, so the document filters hold
for the case base, each member of a folder, and a query graph alike, and the size of the case base and the work of one query
are capped, on top of the limits of its configuration.  A case base is listed
by the stats of its visible files, which key the :class:`CaseBaseCache` of
loaded case bases, and its files are read only to load it anew or to plan a
change on them.  Its configuration is always the one `cbrkit.spec.infer`
infers in memory, a sidecar beside it is never read while the format of
cbrkit's specifications still evolves.

The host binds its models by name (:class:`Bindings`): its embedding model as
the embedder :data:`EMBEDDER`, which an inferred configuration compares long
texts with, and the chat model of the turn as the language model :data:`LANGUAGE_MODEL`.
A configuration naming anything else is refused when it is used.
"""

import threading
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from stat import S_ISREG
from typing import Any

from cachetools import LRUCache
from cbrkit.errors import CbrkitError, Refused
from cbrkit.io import (
    FOLDER_SUFFIXES,
    SUFFIXES,
    detect_files,
    is_casebase,
    load_files,
    members,
)
from cbrkit.spec import Config, Embedders, Models, case_model, infer
from cbrkit.typing import NumpyArray
from exar.tools import Retry
from exar.tools.common import DEFAULT_EXCLUDE_DIRS, read_bytes_or_retry

from ..base import (
    SearchPath,
    entry_visible,
    resolve_accessible_file,
    resolve_file_or_retry,
)
from ..changes import Delete, Operation, Write
from ..entries import ContentStat

__all__ = [
    "EMBEDDER",
    "LANGUAGE_MODEL",
    "Bind",
    "Bindings",
    "CaseBase",
    "CaseBaseCache",
    "Key",
    "Limits",
    "open_case_base",
]

EMBEDDER = "hivegent"
"""The name a configuration refers to the host's embedding model by."""

LANGUAGE_MODEL = "hivegent"
"""The name a configuration refers to the host's chat model by."""


type Key = int | str


@dataclass(slots=True, frozen=True)
class Limits:
    """What one case base and one query may cost.

    Attributes:
        max_bytes: Largest case base on disk, the files of a folder summed.
        max_cases: Most cases a case base may hold.
        max_node_pairs: Most pairs of query and case nodes a graph query compares,
            below the budget of the configured limits.
        max_limit: Most cases one query returns, below the configured maximum.
        max_maintained: Most cases a maintenance run compares pairwise.
    """

    max_bytes: int = 50 * 1024 * 1024
    max_cases: int = 20_000
    max_node_pairs: int = 2_000_000
    max_limit: int = 50
    max_maintained: int = 2_000


_LIMITS = Limits()
"""The default limits."""


@dataclass(slots=True, frozen=True)
class Bindings:
    """The embedders and language models of the host by the names a configuration refers to."""

    embedders: Embedders = field(default_factory=dict)
    models: Models = field(default_factory=dict)

    @property
    def embedder(self) -> str | None:
        """The embedder an inferred configuration compares long texts with, if bound."""
        return EMBEDDER if EMBEDDER in self.embedders else None


type Bind = Callable[[], Awaitable[Bindings]]
"""Gets the bindings of the host when a call needs them."""


async def _bindings(bind: Bind | None) -> Bindings:
    return Bindings() if bind is None else await bind()


@dataclass(slots=True, frozen=True)
class CaseBase:
    """A loaded case base with the configuration inferred from its data.

    Attributes:
        path: Its full workspace path.
        config: The configuration a query is ranked by.
        cases: The cases by their keys.
        query_schema: The JSON Schema of a query, listing the values of a
            categorical attribute and the range of a number, built once on load.
        embeddings: The vectors of its texts by text, kept as long as it is,
            so its cases are embedded once rather than on every query.
    """

    path: str
    config: Config
    cases: Mapping[Key, Any]
    query_schema: Mapping[str, Any] = field(
        default_factory=dict, repr=False, compare=False
    )
    embeddings: dict[str, NumpyArray] = field(
        default_factory=dict, repr=False, compare=False
    )

    @property
    def graphs(self) -> bool:
        """Whether its cases are graphs."""
        return self.config.source.graphs


type _Stats = tuple[tuple[str, ContentStat | None], ...]

type _CacheKey = tuple[Path, _Stats, str | None]


_MEMORY_FACTOR = 4
"""How many times larger than its files a loaded case base is estimated to be in memory."""


def _disk_size(key: _CacheKey) -> int:
    """The bytes the files a case base is loaded from take on disk."""
    _absolute, stats, _embedder = key

    return sum(stat.size for _name, stat in stats if stat)


@dataclass(slots=True)
class CaseBaseCache:
    """The most recently loaded case bases, keyed by what they were loaded from.

    The key is the case base's location, the :class:`~.entries.ContentStat` of
    each of its files by name, and the embedder its inferred configuration
    uses, so any change to the data loads it anew.
    The stats are taken before the files are read, so a change made during a
    load is loaded again by the next call.  Each case base weighs the size of
    its files on disk times :data:`_MEMORY_FACTOR`, at least a share of
    *max_bytes* that leaves room for *max_entries* of them, and the least
    recently used ones are dropped beyond *max_bytes*, while one heavier than
    that is not kept.  Safe to share between threads.

    Attributes:
        max_bytes: Most bytes the kept case bases are estimated to weigh in memory together.
        max_entries: Most case bases kept.
    """

    max_bytes: int = 256 * 1024 * 1024
    max_entries: int = 8
    _entries: LRUCache[_CacheKey, tuple[CaseBase, int]] = field(init=False, repr=False)
    _lock: threading.Lock = field(
        default_factory=threading.Lock, init=False, repr=False
    )

    def __post_init__(self) -> None:
        self._entries = LRUCache(self.max_bytes, getsizeof=lambda entry: entry[1])

    def get(self, key: _CacheKey) -> CaseBase | None:
        """The case base loaded from *key*, if kept."""
        with self._lock:
            entry = self._entries.get(key)

        return None if entry is None else entry[0]

    def put(self, key: _CacheKey, case_base: CaseBase) -> None:
        """Keep *case_base* as loaded from *key*, unless it weighs more than the whole cache."""
        weight = max(
            _disk_size(key) * _MEMORY_FACTOR, self.max_bytes // self.max_entries
        )

        if weight > self.max_bytes:
            return

        with self._lock:
            self._entries[key] = (case_base, weight)


@dataclass(slots=True, frozen=True)
class _File:
    data: bytes
    stat: ContentStat | None
    """Taken before *data* was read, so a change made since is refused on commit."""


@dataclass(slots=True, frozen=True)
class _Snapshot:
    """The visible files of a case base as read once, a single file by its name."""

    canonical: str
    name: str
    folder: bool
    files: Mapping[str, _File]

    @property
    def data(self) -> dict[str, bytes]:
        """The contents of the files by name, as `cbrkit.io.load_files` takes them."""
        return {name: file.data for name, file in self.files.items()}

    def _target(self, name: str) -> str:
        return f"{self.canonical}/{name}" if self.folder else self.canonical

    def operations(self, files: Mapping[Path, bytes | None]) -> list[Operation[str]]:
        """The writes and deletes of the files `cbrkit.io.render` gave below ``Path(name)``."""
        root = Path(self.name)
        operations: list[Operation[str]] = []

        for path, data in sorted(files.items()):
            name = path.relative_to(root).as_posix() if self.folder else self.name
            before = self.files.get(name)

            if data is None:
                if before is not None:
                    operations.append(Delete(self._target(name), before.stat))
            elif before is None:
                operations.append(Write(self._target(name), data.decode(), "create"))
            elif before.data != data:
                operations.append(
                    Write(self._target(name), data.decode(), "replace", before.stat)
                )

        return operations


def _members(
    sp: SearchPath, local: str, absolute: Path, limits: Limits
) -> list[tuple[Path, ContentStat]]:
    """The members of a folder case base by cbrkit's rule that the caller may see, sorted, with their stats.

    The walk skips excluded folders and stops beyond the most cases a case base may hold.
    """
    root = sp.path
    visible: list[tuple[Path, ContentStat]] = []

    try:
        found = members(
            absolute,
            limit=limits.max_cases,
            prune=lambda folder: folder.name in DEFAULT_EXCLUDE_DIRS,
        )
    except Refused as exc:
        raise Retry(_too_many(limits.max_cases)) from exc

    for member in found:
        try:
            st = member.lstat()
        except OSError:
            continue

        if (
            S_ISREG(st.st_mode)
            and entry_visible(
                sp,
                f"{local}/{member.relative_to(absolute).as_posix()}",
                DEFAULT_EXCLUDE_DIRS,
            )
            and member.resolve().is_relative_to(root)
        ):
            visible.append((member, ContentStat.of(st)))

    return visible


def _too_many(limit: int) -> str:
    return f"The case base holds more than the {limit} cases allowed."


def _too_large(limits: Limits) -> str:
    return f"The case base is larger than {limits.max_bytes} bytes, more than a query may load."


@dataclass(slots=True, frozen=True)
class _Listing:
    """The visible files of a case base with their stats, a single file by its name."""

    canonical: str
    search_path: SearchPath
    absolute: Path
    folder: bool
    files: Mapping[str, tuple[Path, ContentStat | None]]

    def key(self, embedder: str | None) -> _CacheKey:
        """What the case base is cached by, see :class:`CaseBaseCache`."""
        return (
            self.absolute,
            tuple((name, stat) for name, (_path, stat) in self.files.items()),
            embedder,
        )

    def _read(self, path: Path) -> bytes:
        """The content of the regular file *path* below the search path, following no symlink.  Blocking."""
        sp = self.search_path

        return read_bytes_or_retry(
            sp.mount, path.relative_to(sp.path).as_posix(), self.canonical
        )

    def read(self) -> _Snapshot:
        """Read the files, each with its stat of the listing.  Blocking.

        Raises:
            Retry: When a file of the case base can no longer be read.
        """
        return _Snapshot(
            self.canonical,
            self.absolute.name,
            self.folder,
            {
                name: _File(self._read(path), stat)
                for name, (path, stat) in self.files.items()
            },
        )


def _resolve(paths: tuple[SearchPath, ...], path: str) -> tuple[SearchPath, str, Path]:
    """The search path, local path, and absolute path of the case base at *path*.  Blocking.

    Raises:
        Retry: When *path* names nothing accessible.
    """
    resolved = resolve_accessible_file(paths, path)

    if resolved is None or not resolved[2].exists():
        # Raises the refusal a file reader owes, naming the roots or a near miss.
        _ = resolve_file_or_retry(paths, path)
        raise Retry(f"'{path}' is no case base.")

    return resolved


def _listing(paths: tuple[SearchPath, ...], path: str, limits: Limits) -> _Listing:
    """Stat the visible files of the case base at *path*.  Blocking.

    Raises:
        Retry: When it is no accessible case base or too large.
    """
    sp, local, absolute = _resolve(paths, path)
    canonical = sp.mount.render(local)
    folder = absolute.is_dir()

    if not is_casebase(absolute):
        raise Retry(
            f"'{canonical}' is no case base. Case bases are "
            f"{', '.join(sorted(SUFFIXES))} files and folders of "
            f"{' or '.join(sorted(FOLDER_SUFFIXES))} files."
        )

    entries = (
        _members(sp, local, absolute, limits)
        if folder
        else [(absolute, ContentStat.from_path(absolute))]
    )
    files = {
        member.relative_to(absolute).as_posix() if folder else member.name: (
            member,
            member_stat,
        )
        for member, member_stat in entries
    }

    if sum(stat.size for _path, stat in files.values() if stat) > limits.max_bytes:
        raise Retry(_too_large(limits))

    return _Listing(canonical, sp, absolute, folder, files)


def _load(snapshot: _Snapshot, limits: Limits, embedder: str | None) -> CaseBase:
    try:
        data = snapshot.data
        source = detect_files(data, folder=snapshot.folder)
        cases = load_files(data, source, limits.max_cases)
        config = infer(cases, source, embedder=embedder)
    except CbrkitError as exc:
        raise Retry(
            f"'{snapshot.canonical}' cannot be read as a case base: {exc}"
        ) from exc

    return CaseBase(
        snapshot.canonical,
        config,
        cases,
        case_model(config, cases=cases).model_json_schema(),
    )


def _open(
    paths: tuple[SearchPath, ...],
    path: str,
    limits: Limits,
    embedder: str | None,
    cache: CaseBaseCache | None,
) -> tuple[CaseBase, _Listing]:
    listing = _listing(paths, path, limits)
    key = listing.key(embedder)
    cached = None if cache is None else cache.get(key)

    if cached is not None:
        return replace(cached, path=listing.canonical), listing

    case_base = _load(listing.read(), limits, embedder)

    if cache is not None:
        cache.put(key, case_base)

    return case_base, listing


def open_case_base(
    paths: tuple[SearchPath, ...],
    path: str,
    limits: Limits,
    *,
    embedder: str | None = None,
    cache: CaseBaseCache | None = None,
) -> CaseBase:
    """Load the case base at *path* with its effective configuration, writing nothing.  Blocking.

    Args:
        paths: The search paths it is resolved against.
        path: Its full workspace path.
        limits: What it may cost.
        embedder: The embedder an inferred configuration compares long texts with.
        cache: Where loaded case bases are kept.

    Raises:
        Retry: When it is no accessible case base or too large.
    """
    return _open(paths, path, limits, embedder, cache)[0]
