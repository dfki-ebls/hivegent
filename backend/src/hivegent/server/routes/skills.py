"""Routes for the skills a user sees, installs, edits, and deletes.

A user's and a group's library take a whole skill as one ZIP archive, which
replaces a skill of its name, or the ``SKILL.md`` of one alone, which keeps
its other files.  A skill is no document, so its ``SKILL.md`` is read here by
the skill's reference, never through ``GET /documents``.  The global skills
come from the operator, so no route changes them.
"""

import asyncio
from pathlib import Path

from fastapi import APIRouter, HTTPException, UploadFile, status
from fastapi.responses import Response

from ...agents.tools.python import check_skill_script
from ...auth import CurrentUser
from ...config import settings
from ...shared.l10n import Localized, format_bytes
from ...shared.skills import SKILL_FILE
from ...skills import (
    ListedSkill,
    Skill,
    delete_skill,
    install_skill,
    read_archive,
    resolve_skill,
    save_skill_document,
    visible_skills,
)
from ...store import User
from ...types import (
    SkillDocumentRequest,
    SkillInfo,
    SkillInstalledResponse,
    SkillScriptInfo,
)
from ..operations import get_file_response, upload_body_limit
from ..rate_limit import UPLOAD_LIMIT

__all__ = ["router"]

router = APIRouter()


def _too_large(limit: int) -> Localized[str]:
    return Localized(
        en=f"A skill archive may hold at most {format_bytes(limit)}",
        de=f"Ein Skill-Archiv darf höchstens {format_bytes(limit)} enthalten",
    )


def _info(skill: Skill, *, writable: bool = True) -> SkillInfo:
    document = skill.document

    return SkillInfo(
        name=skill.name,
        ref=skill.ref,
        path=skill.path,
        scope=skill.scope.prefix,
        description=None if document is None else document.manifest.description,
        problem=skill.problem,
        warnings=[] if document is None else list(document.warnings),
        writable=writable,
        model_invocable=skill.model_invocable,
        user_invocable=skill.user_invocable,
    )


def _listed(entry: ListedSkill) -> SkillInfo:
    return _info(entry.skill, writable=entry.writable)


@router.get("/skills")
async def list_skills(user: CurrentUser) -> list[SkillInfo]:
    """Every skill the user sees: the global ones, each group's, and their own, in that order."""
    return [_listed(entry) for entry in await asyncio.to_thread(visible_skills, user)]


def _skill_document(user: User, ref: str) -> Path | None:
    """The ``SKILL.md`` of the skill *ref* names among those *user* sees, ``None`` for none."""
    skill = resolve_skill((entry.skill for entry in visible_skills(user)), ref)

    return None if skill is None else skill.file(SKILL_FILE)


@router.get("/skills/{ref:path}")
async def get_skill_document(ref: str, user: CurrentUser) -> Response:
    """Get the ``SKILL.md`` of a skill the user sees, by its reference such as ``~/name`` or ``srv/name``."""
    return await get_file_response(lambda: _skill_document(user, ref), ref)


@router.post("/skills/{scope}/{name}", status_code=status.HTTP_201_CREATED)
async def create_skill_document(
    scope: str, name: str, request: SkillDocumentRequest, user: CurrentUser
) -> SkillInfo:
    """Create a skill in the library of *scope*."""
    return _info(await save_skill_document(user, scope, name, request.content, create=True))


@router.put("/skills/{scope}/{name}")
async def put_skill_document(
    scope: str, name: str, request: SkillDocumentRequest, user: CurrentUser
) -> SkillInfo:
    """Save the ``SKILL.md`` of the skill *name* in the library of *scope*, keeping its other files."""
    return _info(await save_skill_document(user, scope, name, request.content))


@router.put(
    "/skills/{scope}",
    dependencies=[UPLOAD_LIMIT],
    max_body_size=upload_body_limit(settings.skills.max_bytes),
)
async def put_skill(
    scope: str, file: UploadFile, user: CurrentUser
) -> SkillInstalledResponse:
    """Install the skill a ZIP archive holds in the library of *scope*, ``~`` or ``@<group>``.

    The archive holds the skill folder or its content, and replaces a skill of
    its name.  Each script is checked by the convention ``run_python`` calls it
    by, and one that does not run still installs, as a reference.
    """
    limit = settings.skills.max_bytes

    if file.size is not None and file.size > limit:
        raise HTTPException(status_code=413, detail=_too_large(limit).current)

    installed = await install_skill(
        user, scope, read_archive(await file.read(limit + 1))
    )
    skill = installed.skill
    scripts = sorted(installed.scripts.items())
    checks = await asyncio.gather(
        *(
            check_skill_script(source, f"{skill.path}/{path}")
            for path, source in scripts
        )
    )

    return SkillInstalledResponse(
        skill=_info(skill),
        scripts=[
            SkillScriptInfo(path=path, signature=check.signature, problem=check.problem)
            for (path, _source), check in zip(scripts, checks, strict=True)
        ],
    )


@router.delete("/skills/{scope}/{name}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_skill(scope: str, name: str, user: CurrentUser) -> None:
    """Delete the skill *name* from the library of *scope*."""
    await delete_skill(user, scope, name)
