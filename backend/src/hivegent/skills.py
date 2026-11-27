"""Agent Skills by scope: where each lives, who sees it, and how one is installed.

A skill (:mod:`hivegent.shared.skills`) belongs to a scope, like a workspace,
but is no document: it lies under its own root, never inside a workspace, so
it is never indexed, searched, cited, or reached by a document tool.  The
agent reads one only through ``load_skill`` and runs its scripts with
``run_python``, and a person installs, edits, and deletes one only through the
REST API, so no agent tool changes a skill.  Its paths are

* ``/skills/srv/<name>``, the global skills, which the operator's
  ``skills.dir`` and the enabled plugins hold in place and nobody changes
  through Hivegent, each skill a read-only root of its own,
* ``/skills/@<group>/<name>``, a group's, which its writers change, and
* ``/skills/~/<name>``, the user's own,

the last two kept in ``<data_dir>/skills/<owner>/<name>/``, a user's or a
group's library a :class:`~hivegent.workspace.operations.Root` under its quota.

The catalog lists the skills a user sees in the order user, groups, global,
each by its reference, its scope's prefix and its name as a document path
spells them, ``/srv/<name>``, ``@<group>/<name>``, or ``~/<name>``, which is
how the model loads one.  A name is unique only within its scope, so no scope
can stand in for another's, whoever can see both.
"""

import asyncio
import io
import threading
import zipfile
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from uuid import uuid4

from cachetools import LRUCache
from cbrkit.io import atomic_write, durable_rename
from exar.memory.mounts import render
from fastapi import HTTPException

from .config import sanitize_document_path, settings
from .files import disk_size, remove_path, transient
from .plugins.loader import LoadedPlugin, merge
from .shared.l10n import Localized, format_bytes
from .shared.skills import SKILL_FILE, InvalidSkill, SkillDocument, parse_skill
from .store import (
    AccessDenied,
    GlobalScope,
    GroupScope,
    Scope,
    User,
    UserScope,
    Workspace,
)
from .tools.base import SearchPath, resolve_accessible_file
from .tools.entries import ContentStat, is_ignorable_path
from .workspace.locks import root_lock
from .workspace.operations import Quota, Root
from .workspace_events import announce_skills_changed

__all__ = [
    "SKILLS_PREFIX",
    "Installed",
    "LibraryGrant",
    "ListedSkill",
    "Skill",
    "catalog",
    "delete_skill",
    "install_skill",
    "library",
    "read_archive",
    "remove_skills_root",
    "resolve_skill",
    "save_skill_document",
    "scope_author",
    "skill_dir",
    "skill_prefix",
    "skill_search_paths",
    "visible_skills",
]

SKILLS_PREFIX = "/skills"
"""What every skill path leads with."""


def _invalid(reason: str) -> Localized[str]:
    return Localized(en=f"Invalid skill: {reason}", de=f"Ungültiger Skill: {reason}")


def _not_found(name: str) -> Localized[str]:
    return Localized(
        en=f"No skill named {name!r} here", de=f"Hier gibt es keinen Skill „{name}“"
    )


def _already_exists(name: str) -> Localized[str]:
    return Localized(
        en=f"A skill named {name!r} already exists here",
        de=f"Hier gibt es bereits einen Skill namens „{name}“",
    )


def _too_large(files: int, size: int) -> Localized[str]:
    most_files, most_bytes = (
        settings.skills.max_files,
        format_bytes(settings.skills.max_bytes),
    )

    return Localized(
        en=(
            f"The skill holds {files} files in {format_bytes(size)}, while one "
            f"may hold {most_files} files in {most_bytes}"
        ),
        de=(
            f"Der Skill enthält {files} Dateien mit {format_bytes(size)}, erlaubt "
            f"sind {most_files} Dateien mit {most_bytes}"
        ),
    )


type LibraryScope = GroupScope | UserScope
"""A scope whose skill library Hivegent keeps, every one but the global."""

_GLOBAL_ONLY = Localized(
    en="The global library comes from the operator and cannot be changed here",
    de="Die globale Bibliothek stellt der Betrieb bereit, sie lässt sich hier nicht ändern",
)


def scope_author(scope: Scope) -> Localized[str]:
    """Who writes the skills of *scope*, as the model is told.

    >>> scope_author(GroupScope("team")).en
    'the group @team'
    """
    match scope:
        case GlobalScope():
            return Localized(en="the operator", de="der Betrieb")

        case GroupScope(id=group_id):
            return Localized(en=f"the group @{group_id}", de=f"die Gruppe @{group_id}")

        case UserScope():
            return Localized(en="the user", de="die Benutzer:in")


def _library_scopes(user: User, *, writable: bool) -> list[LibraryScope]:
    """The library scopes *user* reads, or writes when *writable* is true."""
    return [
        store.scope
        for store in user.workspaces(writable=writable)
        if isinstance(store.scope, GroupScope | UserScope)
    ]


@dataclass(frozen=True, slots=True)
class LibraryGrant:
    """The skill library of a scope, with the write grant of its workspace it is changed under.

    Attributes:
        store: The scope's workspace, granted for writing.
        scope: Its scope, one Hivegent keeps a library for.

    Raises:
        AccessDenied: When *store* is not granted for writing, or is the
            global workspace or a source's.
    """

    store: Workspace
    scope: LibraryScope = field(init=False)

    def __post_init__(self) -> None:
        self.store.require_writable()

        match self.store.scope:
            case GroupScope() | UserScope() as scope:
                object.__setattr__(self, "scope", scope)
            case _:
                raise AccessDenied(_GLOBAL_ONLY.current)


def _writable_scope(user: User, prefix: str) -> LibraryScope:
    """The scope at *prefix*, ``~`` or ``@<group>``, whose library *user* changes.

    Raises:
        HTTPException: ``403`` for the global scope or one the user may not write.
    """
    try:
        return LibraryGrant(user.workspace(prefix, writable=True)).scope
    except AccessDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


def skill_prefix(scope: Scope) -> str:
    """The path every skill of *scope* leads with.

    >>> skill_prefix(GlobalScope()), skill_prefix(GroupScope("team")), skill_prefix(UserScope("u"))
    ('/skills/srv', '/skills/@team', '/skills/~')
    """
    return f"{SKILLS_PREFIX}/{scope.prefix.removeprefix('/')}"


def skill_dir(canonical: str) -> str | None:
    """The skill folder a file's canonical path lies in, ``None`` outside every skill.

    Derived from a path the host resolved, never from what a model claims.

    >>> skill_dir("/skills/~/csv-check/scripts/check.py"), skill_dir("/tmp/a/b/c.py")
    ('/skills/~/csv-check', None)
    """
    parts = PurePosixPath(canonical).parts

    if len(parts) < 5 or parts[:2] != ("/", SKILLS_PREFIX.strip("/")):
        return None

    return str(PurePosixPath(*parts[:4]))


def _skills_root() -> Path:
    return settings.data_dir / "skills"


def _library_quota() -> Quota:
    return Quota(settings.skills.library_max_bytes)


def library(scope: LibraryScope) -> Root:
    """The root holding the skills of *scope*, under the quota of a library."""
    return Root(
        SearchPath.of(_skills_root() / scope.key, skill_prefix(scope)),
        _library_quota(),
    )


@dataclass(frozen=True, slots=True)
class Skill:
    """A skill folder some scope holds, parsed or with the reason it is not.

    Attributes:
        scope: Who owns it.
        folder: The folder on disk.
        document: Its parsed ``SKILL.md``, ``None`` when it is invalid.
        problem: Why it is invalid, ``None`` when it is not.
    """

    scope: Scope
    folder: Path
    document: SkillDocument | None
    problem: str | None = None

    @property
    def name(self) -> str:
        """Its name, the folder's."""
        return self.folder.name

    @property
    def path(self) -> str:
        """The canonical path of its folder."""
        return f"{skill_prefix(self.scope)}/{self.name}"

    @property
    def ref(self) -> str:
        """The reference it is loaded by, such as ``~/csv-check``."""
        return self.scope.render(self.name)

    def file(self, relative: str) -> Path | None:
        """The file at *relative* in its folder, resolved as a path tool resolves it, ``None`` for none.

        No path escapes the folder, a symlink or ``..`` included.  Blocking.
        """
        resolved = resolve_accessible_file(
            (SearchPath.of(self.folder, self.path),),
            f"{self.path}/{relative.lstrip('/')}",
        )

        return None if resolved is None or not resolved[2].is_file() else resolved[2]

    @property
    def model_invocable(self) -> bool:
        """Whether the model may load it, which a valid one may unless it disables model invocation."""
        return (
            self.document is not None
            and not self.document.manifest.disable_model_invocation
        )

    @property
    def user_invocable(self) -> bool:
        """Whether a prompt may invoke it, which a valid one may unless it is not user-invocable."""
        return self.document is not None and self.document.manifest.user_invocable


_PARSED: LRUCache[tuple[Scope, Path], tuple[ContentStat, Skill]] = LRUCache(1024)
"""The skill each folder held when its ``SKILL.md`` was last parsed, one version per folder."""

_PARSED_LOCK = threading.Lock()


def _read(scope: Scope, folder: Path) -> Skill:
    """The skill *folder* holds, invalid when its ``SKILL.md`` is, parsed once per version."""
    try:
        stat = ContentStat.of((folder / SKILL_FILE).stat())

    except FileNotFoundError:
        return Skill(scope, folder, None, f"The folder holds no {SKILL_FILE}.")

    except OSError as exc:
        return Skill(scope, folder, None, str(exc))

    key = (scope, folder)

    with _PARSED_LOCK:
        cached = _PARSED.get(key)

    if cached is not None and cached[0] == stat:
        return cached[1]

    skill = _parse(scope, folder)

    with _PARSED_LOCK:
        _PARSED[key] = (stat, skill)

    return skill


def _parse(scope: Scope, folder: Path) -> Skill:
    """The skill *folder* holds, its ``SKILL.md`` read and parsed."""
    try:
        text = (folder / SKILL_FILE).read_text("utf-8")

        return Skill(scope, folder, parse_skill(text, folder.name))

    except FileNotFoundError:
        return Skill(scope, folder, None, f"The folder holds no {SKILL_FILE}.")

    except (OSError, UnicodeDecodeError, InvalidSkill) as exc:
        return Skill(scope, folder, None, str(exc))


def _folders(directory: Path) -> list[Path]:
    """The skill folders in *directory*, by name, none when it is missing."""
    try:
        return sorted(
            path
            for path in directory.iterdir()
            if path.is_dir() and not path.name.startswith(".")
        )

    except FileNotFoundError:
        return []


def _plugin_skills(loaded: LoadedPlugin) -> Iterator[tuple[str, Path]]:
    """The skill folders plugin *loaded* ships, by name.

    Raises:
        ValueError: For a skill whose ``SKILL.md`` is missing or invalid.
    """
    for directory in loaded.plugin.skills:
        for folder in _folders(directory):
            try:
                _ = parse_skill((folder / SKILL_FILE).read_text("utf-8"), folder.name)
            except (OSError, UnicodeDecodeError, InvalidSkill) as exc:
                raise ValueError(
                    f"Plugin {loaded.name!r} ships the invalid skill {folder.name!r}: {exc}"
                ) from exc

            yield folder.name, folder


_PLUGIN_SKILLS = merge("skill", (), _plugin_skills)
"""The read-only skill folders the enabled plugins ship, by name."""


def _skill_folders(scope: Scope) -> list[Path]:
    """Every skill folder *scope* holds, the operator's global ones shadowing the plugins'."""
    match scope:
        case GlobalScope():
            configured = settings.skills.dir
            folders = [] if configured is None else _folders(configured)
            taken = {folder.name for folder in folders}

            return [
                *folders,
                *(
                    folder
                    for name, folder in _PLUGIN_SKILLS.items()
                    if name not in taken
                ),
            ]

        case GroupScope() | UserScope():
            return _folders(library(scope).path)


def _skills_of(scope: Scope) -> list[Skill]:
    """Every skill *scope* holds, each ``SKILL.md`` parsed."""
    return [_read(scope, folder) for folder in _skill_folders(scope)]


@dataclass(frozen=True, slots=True)
class ListedSkill:
    """A skill as its user sees it, and whether they may replace or delete it."""

    skill: Skill
    writable: bool


def visible_skills(user: User) -> list[ListedSkill]:
    """Every skill *user* sees, their own first, then each group's, then global.

    Blocking, it reads every ``SKILL.md``.
    """
    writes = set(_library_scopes(user, writable=True))

    return [
        ListedSkill(skill, scope in writes)
        for scope in user.scopes()
        for skill in _skills_of(scope)
    ]


def catalog(user: User) -> list[Skill]:
    """The valid skills *user* may load.

    Blocking, see :func:`visible_skills`.
    """
    return [
        entry.skill
        for entry in visible_skills(user)
        if entry.skill.document is not None
    ]


def _breadth(scope: Scope) -> int:
    """How wide the audience of *scope* is, the user's own narrowest and everyone's widest."""
    match scope:
        case UserScope():
            return 0
        case GroupScope():
            return 1
        case _:
            return 2


def resolve_skill(skills: Iterable[Skill], word: str) -> Skill | None:
    """The skill of *skills* that *word* names, by its reference or by its name.

    A global skill's reference may drop its leading slash, as a prompt that
    invokes one by ``/<word>`` spells it.  A bare name names the skill of the
    narrowest scope that has one, so the user's own skill wins over a group's
    and a group's over a global one, and none while two groups have it.  No
    one can thus make a skill of a wider audience stand in for the user's own.

    >>> skills = [Skill(GlobalScope(), Path("a"), None), Skill(UserScope("u"), Path("a"), None)]
    >>> [resolve_skill(skills, word).ref for word in ("a", "~/a", "srv/a", "/srv/a")]
    ['~/a', '~/a', '/srv/a', '/srv/a']
    >>> groups = [Skill(GroupScope("x"), Path("b"), None), Skill(GroupScope("y"), Path("b"), None)]
    >>> resolve_skill(groups, "b") is None
    True
    """
    skills = list(skills)

    if exact := [s for s in skills if word in (s.ref, s.ref.removeprefix("/"))]:
        return exact[0]

    named = [s for s in skills if s.name == word]

    if not named:
        return None

    narrowest = min(_breadth(s.scope) for s in named)
    found = [s for s in named if _breadth(s.scope) == narrowest]

    return found[0] if len(found) == 1 else None


def skill_search_paths(user: User) -> tuple[SearchPath, ...]:
    """Every skill root *user* reads: every library, then each global skill a root of its own.

    Blocking, it lists the global skill directories.
    """
    prefix = skill_prefix(GlobalScope())
    global_skills = (
        SearchPath.of(path=folder, target=render(prefix, folder.name))
        for folder in _skill_folders(GlobalScope())
    )
    libraries = (
        library(scope).search_path for scope in _library_scopes(user, writable=False)
    )

    return (*libraries, *global_skills)


def _top(paths: Iterable[str]) -> str | None:
    """The one folder every path lies in, ``None`` when they share none.

    >>> _top(["a/SKILL.md", "a/x.py"]), _top(["SKILL.md", "a/x.py"])
    ('a', None)
    """
    parts = [PurePosixPath(path).parts for path in paths]
    tops = {part[0] for part in parts}

    return tops.pop() if len(tops) == 1 and all(len(p) > 1 for p in parts) else None


@dataclass(frozen=True, slots=True)
class Bundle:
    """An uploaded skill, validated.

    Attributes:
        document: Its parsed ``SKILL.md``.
        files: Every file by its path in the skill folder.
        scripts: Every ``.py`` decoded, by the same path, for its check.
    """

    document: SkillDocument
    files: Mapping[str, bytes]
    scripts: Mapping[str, str]


def _bundle(files: Mapping[str, bytes]) -> Bundle:
    """Validate an uploaded skill.

    The paths are sanitized, OS junk dropped, and a folder every file lies in
    stripped, which must then be named for the skill.

    Raises:
        HTTPException: ``422`` for an invalid skill, ``413`` for one too large.
    """
    try:
        cleaned = {
            sanitize_document_path(path): data
            for path, data in files.items()
            if not is_ignorable_path(path.replace("\\", "/"))
        }

    except ValueError as exc:
        raise HTTPException(status_code=422, detail=_invalid(str(exc)).current) from exc

    if (top := _top(cleaned)) is not None:
        cleaned = {path.removeprefix(f"{top}/"): data for path, data in cleaned.items()}

    size = sum(map(len, cleaned.values()))

    if len(cleaned) > settings.skills.max_files or size > settings.skills.max_bytes:
        raise HTTPException(
            status_code=413, detail=_too_large(len(cleaned), size).current
        )

    if SKILL_FILE not in cleaned:
        reason = f"no {SKILL_FILE} at the top of the skill"
        raise HTTPException(status_code=422, detail=_invalid(reason).current)

    try:
        document = parse_skill(cleaned[SKILL_FILE].decode("utf-8"), top)
        scripts = {
            path: data.decode("utf-8")
            for path, data in cleaned.items()
            if PurePosixPath(path).suffix == ".py"
        }

    except (UnicodeDecodeError, InvalidSkill) as exc:
        raise HTTPException(status_code=422, detail=_invalid(str(exc)).current) from exc

    return Bundle(document, cleaned, scripts)


def read_archive(data: bytes) -> dict[str, bytes]:
    """The files of a ZIP archive by path, its directories left out.

    The sizes its members declare are checked before any is inflated, and
    each is read no further than one skill may hold, so a bomb is refused.

    Raises:
        HTTPException: ``422`` for no ZIP archive, ``413`` for one too large.
    """
    limit = settings.skills.max_bytes

    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            members = [info for info in archive.infolist() if not info.is_dir()]
            size = sum(info.file_size for info in members)

            if len(members) > settings.skills.max_files or size > limit:
                detail = _too_large(len(members), size).current
                raise HTTPException(status_code=413, detail=detail)

            files: dict[str, bytes] = {}

            for info in members:
                with archive.open(info) as member:
                    files[info.filename] = member.read(limit + 1)

            return files

    except zipfile.BadZipFile as exc:
        raise HTTPException(status_code=422, detail=_invalid(str(exc)).current) from exc


def _staging_root() -> Path:
    """On the skills' filesystem and emptied at boot, as the data directory's transient folder."""
    return transient("skills")


def _staging() -> Path:
    """A fresh folder on the skills' filesystem, from which a skill moves in whole."""
    path = _staging_root() / uuid4().hex
    path.mkdir(parents=True)

    return path


def _write(
    files: Mapping[str, bytes],
    target: Path,
    rename: Callable[[Path, Path], object] = durable_rename,
) -> None:
    """Put *files* at *target* in one *rename*, replacing the skill there, durably."""
    staged = _staging()

    try:
        fresh = staged / "skill"

        for path, data in files.items():
            atomic_write(fresh / path, data, sync_folder=False)

        target.parent.mkdir(parents=True, exist_ok=True)

        retired = staged / "retired"

        if target.exists():
            rename(target, retired)

        try:
            rename(fresh, target)
        except BaseException:
            if retired.exists():
                rename(retired, target)

            raise

    finally:
        remove_path(staged)


async def _change[T](
    user: User, prefix: str, change: Callable[[Root], T]
) -> tuple[LibraryScope, T]:
    """Run *change* on the library of the scope at *prefix* under its lock, off the event loop, then announce it.

    Raises:
        HTTPException: ``403`` for a library the user may not change.
    """
    scope = _writable_scope(user, prefix)
    root = library(scope)

    async with root_lock(root):
        result = await asyncio.to_thread(change, root)

    announce_skills_changed(scope)

    return scope, result


def _admit(root: Root, target: Path, size: int) -> None:
    """Refuse a write of *size* bytes in place of *target* that would grow *root* over its quota."""
    _library_quota().admit(
        root.path, root.mount.target, size - disk_size(target), frozenset(), evict=False
    )


@dataclass(frozen=True, slots=True)
class Installed:
    """A skill just installed, with its scripts for the caller to check."""

    skill: Skill
    scripts: Mapping[str, str]


async def install_skill(
    user: User, prefix: str, files: Mapping[str, bytes]
) -> Installed:
    """Install the skill *files* hold in the library of the scope at *prefix*, replacing one of its name.

    Raises:
        HTTPException: For a library the user may not change, or an invalid
            or too large skill.
        ChangeRefused: When the library would grow over its quota.
    """
    bundle = _bundle(files)

    def write(root: Root) -> Path:
        target = root.path / bundle.document.manifest.name
        _admit(root, target, sum(map(len, bundle.files.values())))
        _write(bundle.files, target)

        return target

    scope, target = await _change(user, prefix, write)

    return Installed(Skill(scope, target, bundle.document), bundle.scripts)


async def save_skill_document(
    user: User, prefix: str, name: str, content: str, *, create: bool = False
) -> Skill:
    """Save *content* as the ``SKILL.md`` of the skill *name* in the library at *prefix*, keeping its other files.

    To *create* it refuses a name the library holds, so it never replaces a skill.

    Raises:
        HTTPException: For a library the user may not change, ``422`` for a
            ``SKILL.md`` that breaks the format or names another skill, ``409``
            for a skill to create whose name is taken.
        ChangeRefused: When the library would grow over its quota.
    """
    try:
        document = parse_skill(content, name)
    except InvalidSkill as exc:
        raise HTTPException(status_code=422, detail=_invalid(str(exc)).current) from exc

    data = content.encode()

    def write(root: Root) -> Path:
        folder = root.path / name

        if create and folder.exists(follow_symlinks=False):
            raise HTTPException(status_code=409, detail=_already_exists(name).current)

        _admit(root, folder / SKILL_FILE, len(data))
        atomic_write(folder / SKILL_FILE, data)

        return folder

    scope, folder = await _change(user, prefix, write)

    return Skill(scope, folder, document)


async def delete_skill(user: User, prefix: str, name: str) -> None:
    """Delete the skill *name* from the library of the scope at *prefix*.

    Raises:
        HTTPException: For a library the user may not change, or no such skill.
    """

    def remove(root: Root) -> None:
        if name not in {path.name for path in _folders(root.path)}:
            raise HTTPException(status_code=404, detail=_not_found(name).current)

        staged = _staging()
        (root.path / name).rename(staged / name)
        remove_path(staged)

    _ = await _change(user, prefix, remove)


async def remove_skills_root() -> None:
    """Remove every user's and group's skills, for a factory reset."""
    await asyncio.to_thread(remove_path, _skills_root())
