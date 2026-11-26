"""Agent Skills by scope: where each lives, who sees it, and how one is installed.

A skill (:mod:`hivegent.shared.skills`) belongs to a scope, like a workspace,
and is addressed under its own root beside ``/tmp``, never inside a
workspace, so it is never indexed, searched, or cited:

* ``/skills/srv/<name>``, the global skills, which the operator's
  ``skills.dir`` and the enabled plugins hold in place and nobody changes
  through Hivegent, each skill a read-only root of its own,
* ``/skills/@<group>/<name>``, a group's, which its writers change, and
* ``/skills/~/<name>``, the user's own,

the last two kept in ``<data_dir>/skills/<owner>/<name>/``.  A user's or a
group's library is a :class:`~hivegent.workspace.operations.Root` whose
policy is :class:`~hivegent.tools.base.Reviewed`, so the agent writes a skill
only with the user's approval, in every mode, while the user installs one
whole through the REST API.

The catalog lists the skills a user sees in the order user, groups, global,
each by its reference (:func:`~hivegent.libraries.library_ref`), its scope's
prefix and its name, which is how the model loads one, so no scope can stand
in for another's.
"""

import asyncio
import io
import threading
import zipfile
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from uuid import uuid4

from cachetools import LRUCache
from cbrkit.io import atomic_write, durable_rename
from exar.memory.mounts import render
from fastapi import HTTPException

from .config import sanitize_document_path, settings
from .files import disk_size, remove_path, transient
from .libraries import (
    LibraryScope,
    Listed,
    library_ref,
    library_scopes,
    visible,
    writable_library,
)
from .plugins.loader import LoadedPlugin, merge
from .shared.l10n import Localized, format_bytes
from .shared.skills import SKILL_FILE, InvalidSkill, SkillDocument, parse_skill
from .store import GlobalScope, GroupScope, Scope, User, UserScope
from .tools.base import Reviewed, SearchPath
from .tools.entries import ContentStat, is_ignorable_path
from .workspace.locks import root_lock
from .workspace.operations import Quota, Root
from .workspace_events import announce_library_changed

__all__ = [
    "SKILLS_PREFIX",
    "Installed",
    "Skill",
    "catalog",
    "delete_skill",
    "install_skill",
    "library",
    "read_archive",
    "remove_skills_root",
    "resolve_skill",
    "skill_dir",
    "skill_prefix",
    "skill_search_paths",
    "visible_skills",
    "writable_libraries",
]

SKILLS_PREFIX = "/skills"
"""What every skill path leads with, beside ``/tmp``."""

_REVIEWED = Reviewed()


def _invalid(reason: str) -> Localized[str]:
    return Localized(en=f"Invalid skill: {reason}", de=f"Ungültiger Skill: {reason}")


def _not_found(name: str) -> Localized[str]:
    return Localized(
        en=f"No skill named {name!r} here", de=f"Hier gibt es keinen Skill „{name}“"
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
    """The root holding the skills of *scope*, every change to it reviewed and announced."""
    return Root(
        SearchPath.of(
            _skills_root() / scope.key, skill_prefix(scope), policy=_REVIEWED
        ),
        _library_quota(),
        on_commit=lambda: announce_library_changed("skills", scope),
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
        return library_ref(self)

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


def writable_libraries(user: User) -> tuple[Root, ...]:
    """The libraries *user* may change: their own, and every group's they write."""
    return tuple(library(scope) for scope in library_scopes(user, writable=True))


def visible_skills(user: User) -> list[Listed[Skill]]:
    """Every skill *user* sees, their own first, then each group's, then global.

    Blocking, it reads every ``SKILL.md``.
    """
    return visible(user, _skills_of)


def catalog(user: User) -> list[Skill]:
    """The valid skills *user* may load.

    Blocking, see :func:`visible_skills`.
    """
    return [
        entry.item for entry in visible_skills(user) if entry.item.document is not None
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
        SearchPath.of(
            path=folder,
            target=render(prefix, folder.name),
            policy=_REVIEWED,
        )
        for folder in _skill_folders(GlobalScope())
    )
    libraries = (
        library(scope).search_path for scope in library_scopes(user, writable=False)
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


def _write(files: Mapping[str, bytes], target: Path) -> None:
    """Put *files* at *target* in one rename, replacing the skill there, durably."""
    staged = _staging()

    try:
        fresh = staged / "skill"

        for path, data in files.items():
            atomic_write(fresh / path, data, sync_folder=False)

        target.parent.mkdir(parents=True, exist_ok=True)

        retired = staged / "retired"

        if target.exists():
            durable_rename(target, retired)

        try:
            durable_rename(fresh, target)
        except BaseException:
            if retired.exists():
                durable_rename(retired, target)

            raise

    finally:
        remove_path(staged)


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
    scope = writable_library(user, prefix).scope
    root = library(scope)
    bundle = _bundle(files)
    name = bundle.document.manifest.name
    target = root.path / name

    def write() -> None:
        growth = sum(map(len, bundle.files.values())) - disk_size(target)
        _library_quota().admit(
            root.path, root.mount.target, growth, frozenset(), evict=False
        )
        _write(bundle.files, target)

    async with root_lock(root):
        await asyncio.to_thread(write)

    announce_library_changed("skills", scope)

    return Installed(Skill(scope, target, bundle.document), bundle.scripts)


async def delete_skill(user: User, prefix: str, name: str) -> None:
    """Delete the skill *name* from the library of the scope at *prefix*.

    Raises:
        HTTPException: For a library the user may not change, or no such skill.
    """
    scope = writable_library(user, prefix).scope
    root = library(scope)

    def remove() -> None:
        if name not in {path.name for path in _folders(root.path)}:
            raise HTTPException(status_code=404, detail=_not_found(name).current)

        staged = _staging()
        (root.path / name).rename(staged / name)
        remove_path(staged)

    async with root_lock(root):
        await asyncio.to_thread(remove)

    announce_library_changed("skills", scope)


async def remove_skills_root() -> None:
    """Remove every user's and group's skills, for a factory reset."""
    await asyncio.to_thread(remove_path, _skills_root())
