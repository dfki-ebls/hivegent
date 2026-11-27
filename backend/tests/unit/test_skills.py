"""Skills: what a user sees, what they may install, and how the agent meets them."""

import io
import zipfile
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from exar.tools import Retry
from fastapi import HTTPException, UploadFile
from pydantic_ai import Agent, ModelRetry
from pydantic_ai.messages import (
    CompactionPart,
    ModelMessage,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_monty import AsyncMonty

import hivegent.agents.tools.python as python_tools
import hivegent.skills as skills_module
from hivegent.agents.common import UserDeps
from hivegent.agents.tools.skills import (
    SKILLS_NOTE,
    load_skill,
    read_skill_file,
    skills_toolset,
)
from hivegent.agents.tools.write import validate_document_write
from hivegent.config import settings
from hivegent.server.routes.skills import (
    create_skill_document,
    get_skill_document,
    put_skill,
    put_skill_document,
)
from hivegent.shared.skills import InvalidSkill, parse_skill
from hivegent.skills import (
    catalog,
    delete_skill,
    install_skill,
    read_archive,
    resolve_skill,
    visible_skills,
)
from hivegent.store import GroupScope, Scope, User, UserScope
from hivegent.tools.changes import ChangeRefused
from hivegent.types import SkillDocumentRequest
from tests.helpers import owned, run_context

USER = User("u", read_groups=frozenset({"archive"}), write_groups=frozenset({"team"}))

SCRIPT = '''"""Count the rows of a table."""
import os

def main(rows: list[str], label: str = "rows") -> str:
    return f"{len(rows)} {label} in {os.environ['SKILL_DIR']}"
'''


def _skill(name: str, description: str = "Counts things.") -> str:
    return f"---\nname: {name}\ndescription: {description}\n---\nCount carefully.\n"


def _zip(files: dict[str, str]) -> bytes:
    buffer = io.BytesIO()

    with zipfile.ZipFile(buffer, "w") as archive:
        for path, text in files.items():
            archive.writestr(path, text)

    return buffer.getvalue()


@pytest.fixture()
def operator_skills(data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An operator directory holding the global skill ``pdf``."""
    directory = data_dir / "operator"
    (directory / "pdf").mkdir(parents=True)
    (directory / "pdf" / "SKILL.md").write_text(_skill("pdf"))
    monkeypatch.setattr(settings.skills, "dir", directory)

    return directory


def test_a_skill_keeps_to_the_format() -> None:
    document = parse_skill(
        _skill("count").replace("---\nCount", "x-other: 1\n---\nCount")
    )
    assert document.warnings == (
        "The frontmatter key 'x-other' is no field of the specification and is ignored.",
    )

    with pytest.raises(InvalidSkill, match="but its folder 'other'"):
        _ = parse_skill(_skill("count"), "other")


def test_the_frontmatter_reads_values_as_written_and_refuses_a_repeated_key() -> None:
    manifest = parse_skill(
        _skill("count").replace(
            "---\nCount",
            "metadata:\n  version: 1.0\n  on: yes\nuser-invocable: False\n---\nCount",
        )
    ).manifest
    assert manifest.metadata == {"version": "1.0", "on": "yes"}
    assert not manifest.user_invocable and not manifest.disable_model_invocation

    with pytest.raises(InvalidSkill, match="repeats the key 'name'"):
        _ = parse_skill(
            _skill("count").replace("---\nCount", "name: other\n---\nCount")
        )

    with pytest.raises(InvalidSkill, match="user-invocable: .* must be true or false"):
        _ = parse_skill(
            _skill("count").replace("---\nCount", "user-invocable: no\n---\nCount")
        )


async def test_install_lists_and_deletes_by_scope(
    operator_skills: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    announced: list[Scope] = []
    monkeypatch.setattr(skills_module, "announce_skills_changed", announced.append)
    archive = _zip({"count/SKILL.md": _skill("count"), "count/scripts/run.py": SCRIPT})
    installed = await install_skill(USER, "~", read_archive(archive))

    assert installed.skill.path == "/skills/~/count"
    assert list(installed.scripts) == ["scripts/run.py"]

    group = await install_skill(USER, "@team", {"SKILL.md": _skill("count").encode()})
    assert group.skill.path == "/skills/@team/count"

    listed = [(e.skill.ref, e.writable) for e in visible_skills(USER)]
    assert listed == [("~/count", True), ("@team/count", True), ("/srv/pdf", False)]

    # A bare name names the narrowest scope's skill, so a group never stands in for the user.
    skills = catalog(USER)
    assert [
        None if (found := resolve_skill(skills, word)) is None else found.ref
        for word in ("count", "@team/count", "pdf", "srv/pdf")
    ] == ["~/count", "@team/count", "/srv/pdf", "/srv/pdf"]

    await delete_skill(USER, "@team", "count")
    assert [skill.ref for skill in catalog(USER)] == ["~/count", "/srv/pdf"]
    assert announced == [UserScope("u"), GroupScope("team"), GroupScope("team")]


@pytest.mark.parametrize(
    ("prefix", "files", "status"),
    [
        ("/srv", {"SKILL.md": _skill("x")}, 403),
        ("@archive", {"SKILL.md": _skill("x")}, 403),
        ("~", {"x/SKILL.md": _skill("y")}, 422),
        ("~", {"README.md": "no skill"}, 422),
    ],
)
async def test_install_refuses_what_the_user_may_not_place(
    operator_skills: Path, prefix: str, files: dict[str, str], status: int
) -> None:
    encoded = {path: text.encode() for path, text in files.items()}

    with pytest.raises(HTTPException) as exc:
        _ = await install_skill(USER, prefix, encoded)

    assert exc.value.status_code == status


async def test_install_keeps_to_the_library_quota(
    data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings.skills, "library_max_bytes", 100)

    with pytest.raises(ChangeRefused) as exc:
        _ = await install_skill(USER, "~", {"SKILL.md": _skill("x" * 64).encode()})

    assert exc.value.status_code == 413
    assert catalog(USER) == []


def _reply(_messages: list[ModelMessage], _info: AgentInfo) -> ModelResponse:
    return ModelResponse(parts=[TextPart(content="ok")])


async def test_the_host_loads_an_invoked_skill_and_again_after_a_compaction(
    operator_skills: Path,
) -> None:
    agent = Agent(deps_type=UserDeps, toolsets=[skills_toolset.toolset()])
    history: list[ModelMessage] = []

    for prompt, compacted in (("/pdf read it", False), ("/nope thanks", True)):
        if compacted:
            history.append(ModelResponse(parts=[CompactionPart(content="the gist")]))

        result = await agent.run(
            prompt,
            model=FunctionModel(_reply),
            deps=UserDeps(user=USER, mode="interactive"),
            message_history=history,
            capabilities=[SKILLS_NOTE],
        )
        history = result.all_messages()

    assert [
        part.args_as_dict().get("name")
        for message in history
        for part in message.parts
        if isinstance(part, ToolCallPart)
    ] == [None, "/srv/pdf", None, "/srv/pdf"]


async def test_invocation_flags_gate_the_model_and_the_user_and_fill_arguments(
    operator_skills: Path,
) -> None:
    for name, flag in (
        ("manual", "disable-model-invocation: true"),
        ("auto", "user-invocable: false"),
    ):
        (operator_skills / name).mkdir()
        (operator_skills / name / "SKILL.md").write_text(
            _skill(name).replace(
                "---\nCount carefully.", f"{flag}\n---\nReview $ARGUMENTS now."
            )
        )

    deps = UserDeps(user=USER, mode="interactive")
    listing = await load_skill(run_context(deps))
    assert "/srv/auto" in listing and "/srv/manual" not in listing

    with pytest.raises(ModelRetry):
        _ = await load_skill(run_context(deps), "manual")

    agent = Agent(deps_type=UserDeps, toolsets=[skills_toolset.toolset()])
    loaded: list[str] = []

    for prompt in ("/manual src/app.py", "/auto x"):
        result = await agent.run(
            prompt, model=FunctionModel(_reply), deps=deps, capabilities=[SKILLS_NOTE]
        )
        loaded.extend(
            part.model_response_str()
            for message in result.all_messages()
            for part in message.parts
            if isinstance(part, ToolReturnPart) and part.content != listing
        )

    assert len(loaded) == 1 and "Review src/app.py now." in loaded[0]


async def test_a_manual_skill_exposes_its_files_once_the_user_invoked_it(
    data_dir: Path,
) -> None:
    manual = _skill("manual").replace(
        "---\nCount", "disable-model-invocation: true\n---\nCount"
    )
    _ = await install_skill(
        USER, "~", {"SKILL.md": manual.encode(), "guide.md": b"Guide."}
    )
    deps = UserDeps(user=USER, mode="interactive")
    read = ToolCallPart("read_skill_file", {"name": "~/manual", "file": "guide.md"})

    with pytest.raises(ModelRetry):
        _ = await read_skill_file(run_context(deps), "~/manual", "guide.md")

    def returned(messages: list[ModelMessage]) -> list[object]:
        return [
            part.content
            for message in messages
            for part in message.parts
            if isinstance(part, ToolReturnPart) and part.tool_name == read.tool_name
        ]

    def model(messages: list[ModelMessage], _info: AgentInfo) -> ModelResponse:
        return ModelResponse(parts=[TextPart("done")] if returned(messages) else [read])

    agent = Agent(deps_type=UserDeps, toolsets=[skills_toolset.toolset()])
    result = await agent.run(
        "/manual", model=FunctionModel(model), deps=deps, capabilities=[SKILLS_NOTE]
    )

    assert returned(result.all_messages()) == ["Guide."]


async def test_creating_a_shared_skill_rejects_an_existing_name(data_dir: Path) -> None:
    original = SkillDocumentRequest(content=_skill("count"))
    _ = await create_skill_document("@team", "count", original, USER)

    with pytest.raises(HTTPException) as exc:
        _ = await create_skill_document(
            "@team",
            "count",
            SkillDocumentRequest(content=_skill("count", "Replacement")),
            USER,
        )

    assert exc.value.status_code == 409
    response = await get_skill_document("@team/count", USER)
    assert bytes(response.body).decode() == original.content


async def test_a_long_skill_loads_cut_short_pointing_at_its_file(
    operator_skills: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The host's loads share the bound of ``load_skill``, which they bypass the spill of."""
    monkeypatch.setattr(settings.llm, "tool_output_max_chars", 500)
    (operator_skills / "pdf" / "SKILL.md").write_text(_skill("pdf") + "x" * 10_000)

    loaded = await load_skill(run_context(UserDeps(user=USER, mode="read")), "pdf")

    assert len(loaded) < 1_000
    assert "read the rest from /skills/srv/pdf/SKILL.md" in loaded


async def test_a_skill_is_no_document(data_dir: Path) -> None:
    """No document tool reaches a skill, which only load_skill reads, file by file."""
    _ = await install_skill(
        USER,
        "~",
        {"SKILL.md": _skill("count").encode(), "scripts/run.py": SCRIPT.encode()},
    )
    deps = UserDeps(user=USER, mode="write", conversation=owned("c1", USER))
    context = run_context(deps)

    with pytest.raises(Retry, match="not accessible"):
        await validate_document_write(
            context, "/skills/~/count/SKILL.md", _skill("count")
        )

    assert await read_skill_file(context, "count", "scripts/run.py") == SCRIPT

    with pytest.raises(ModelRetry, match="holds no file"):
        _ = await read_skill_file(context, "count", "../../other/SKILL.md")


@pytest.fixture()
async def pool(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[AsyncMonty]:
    async with AsyncMonty(min_processes=1) as pool:
        monkeypatch.setattr(python_tools, "get_monty_pool", lambda: pool)
        yield pool


async def test_a_skill_loads_with_its_scripts_checked(
    operator_skills: Path, pool: AsyncMonty
) -> None:
    files = {
        "SKILL.md": _skill("count").encode(),
        "scripts/run.py": SCRIPT.encode(),
        "scripts/broken.py": b"x = 1\n",
    }
    _ = await install_skill(USER, "~", files)
    context = run_context(UserDeps(user=USER, mode="read"))

    listing = await load_skill(context)
    assert "- ~/count (/skills/~/count, by the user): Counts things." in listing

    loaded = await load_skill(context, "~/count")
    assert "Count carefully." in loaded
    assert "- scripts/broken.py: reference only" in loaded
    assert (
        "- scripts/run.py: runnable with run_python(script_path="
        "'/skills/~/count/scripts/run.py', args={...}) calling "
        "main(rows: list[str], label: str='rows') -> str. Count the rows of a table."
    ) in loaded


async def test_run_python_calls_main_with_args_in_its_skill(
    data_dir: Path, pool: AsyncMonty
) -> None:
    _ = await install_skill(
        USER,
        "~",
        {"SKILL.md": _skill("count").encode(), "scripts/run.py": SCRIPT.encode()},
    )
    deps = UserDeps(user=USER, mode="read", conversation=owned("c1", USER))
    tool = python_tools._run_python(run_context(deps))
    script = "/skills/~/count/scripts/run.py"

    result = await tool(script_path=script, args={"rows": ["a", "b"]})
    assert result.metadata.result == "'2 rows in /skills/~/count'"

    with pytest.raises(Retry, match="Argument to function `main` is incorrect"):
        _ = await tool(script_path=script, args={"rows": [], "label": 3})


async def test_the_routes_install_a_skill_and_read_its_files(
    data_dir: Path, pool: AsyncMonty
) -> None:
    archive = _zip({"count/SKILL.md": _skill("count"), "count/scripts/run.py": SCRIPT})
    upload = UploadFile(io.BytesIO(archive), size=len(archive), filename="count.zip")

    installed = await put_skill("~", upload, USER)

    assert installed.skill.path == "/skills/~/count"
    assert [(s.path, s.problem) for s in installed.scripts] == [
        ("scripts/run.py", None)
    ]

    response = await get_skill_document("~/count", USER)
    assert bytes(response.body).decode() == _skill("count")

    # Editing the SKILL.md alone keeps the other files, and it names its folder.
    edited = _skill("count", "Counts rows.")
    info = await put_skill_document(
        "~", "count", SkillDocumentRequest(content=edited), USER
    )
    assert info.description == "Counts rows."
    response = await get_skill_document("count", USER)
    assert "Counts rows." in bytes(response.body).decode()
    assert (
        skills_module.library(UserScope("u")).path / "count/scripts/run.py"
    ).is_file()

    with pytest.raises(HTTPException) as exc:
        _ = await put_skill_document(
            "~", "count", SkillDocumentRequest(content=_skill("other")), USER
        )
    assert exc.value.status_code == 422


def test_a_failed_replacement_keeps_the_installed_skill(
    data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = data_dir / "skills" / "count"
    skills_module._write({"SKILL.md": b"old"}, target)
    rename = skills_module.durable_rename

    def fail_install(source: Path, destination: Path) -> None:
        if source.name == "skill":
            raise OSError("disk full")

        rename(source, destination)

    monkeypatch.setattr(skills_module, "durable_rename", fail_install)

    with pytest.raises(OSError, match="disk full"):
        skills_module._write({"SKILL.md": b"new"}, target)

    assert (target / "SKILL.md").read_bytes() == b"old"
