"""Agent smoke tests using TestModel."""

from pathlib import Path

from pydantic_ai import capture_run_messages
from pydantic_ai.models.test import TestModel

from hivegent.agent import UserDeps, explore_agent, explore_toolset, user_agent
from hivegent.store import Casebase


def _make_deps(data_dir: Path) -> UserDeps:
    store = Casebase(kind="user", id="testuser")
    return UserDeps(user_id="testuser", store=store)


async def test_explore_agent_runs_without_error(data_dir: Path) -> None:
    """Explore agent with TestModel runs without raising."""
    deps = _make_deps(data_dir)

    with capture_run_messages() as messages:
        result = await explore_agent.run(
            "List available documents",
            model=TestModel(),
            deps=deps,
            toolsets=[explore_toolset],
        )

    assert isinstance(result.output, str)
    assert len(messages) > 0


async def test_explore_agent_calls_tools(data_dir: Path) -> None:
    """Explore agent with TestModel calls at least one tool."""
    deps = _make_deps(data_dir)

    # Create a document so list_documents has something to return
    docs_dir = deps.store.documents_dir(data_dir)
    (docs_dir / "test.md").write_text("hello world")

    with capture_run_messages() as messages:
        await explore_agent.run(
            "List available documents",
            model=TestModel(custom_output_text="Done exploring"),
            deps=deps,
            toolsets=[explore_toolset],
        )

    # Check that tool calls happened
    tool_calls = [
        part for msg in messages for part in msg.parts if hasattr(part, "tool_name")
    ]
    assert len(tool_calls) > 0


async def test_user_agent_returns_string(data_dir: Path) -> None:
    """User agent with TestModel returns a string output."""
    deps = _make_deps(data_dir)
    # Use explore_toolset instead of rag_toolset to avoid
    # explore_documents calling a real LLM.
    result = await user_agent.run(
        "Hello",
        model=TestModel(custom_output_text="Hello from test"),
        deps=deps,
        toolsets=[explore_toolset],
    )
    assert isinstance(result.output, str)
