"""Unit tests for the document, search, mutation, and sandbox tools."""

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from functools import partial
from pathlib import Path

import pytest
from exar.ingest import VISION_MEDIA_TYPES
from exar.memory.mounts import Mount
from exar.tools import BatchData, ItemFailure, Retry
from exar.tools.batch import BatchShare, serve_batch, tool_return, unpack
from exar.tools.files import DocumentRange, DocumentRead, GrepMatch
from exar.tools.media import BinaryRead
from PIL import Image
from pydantic import TypeAdapter
from pydantic_ai import BinaryContent, ToolReturn
from pydantic_monty import AsyncMonty

from hivegent.agents.tools.explore import (
    grep,
    jq,
    list_documents,
    read_binary_document,
    read_document,
)
from hivegent.shared.formats import BinaryContentMode
from hivegent.store import HostGrant, UserScope
from hivegent.tools.base import SearchPath, ToolFunc, mounts_of, resolve_accessible_file
from hivegent.tools.changes import (
    ChangeRefused,
    Changeset,
    ChangesetSummary,
    Delete,
    Edit,
    Move,
    TextEdit,
    Write,
)
from hivegent.tools.changeset import ChangesetOutcome, PendingChanges
from hivegent.tools.mutations import (
    Commit,
    DocumentMove,
    delete_documents,
    edit_document,
    move_documents,
    write_document,
)
from hivegent.tools.python import PythonResult, run_python
from hivegent.types import DocumentFilter, FilterEntry
from hivegent.workspace import Gateway, Root
from tests.helpers import LIMITS, doc, folder, single

BINARY = b"PK\x03\x04\xec\xec binary"


def _bare(path: Path) -> tuple[Mount, ...]:
    """The one unprefixed workspace at *path*."""
    return mounts_of((SearchPath.of(path),))


def _home(path: Path) -> tuple[Mount, ...]:
    """The personal workspace at *path*, whose paths may lead with ``~``."""
    return mounts_of((SearchPath.of(path=path, target="~", implied=True),))


def _scoped_home(root: Path) -> SearchPath:
    """A personal-workspace root that hides ``excluded.md``."""
    return SearchPath.of(
        path=root,
        target="~",
        implied=True,
        filter_func=DocumentFilter(excluded=frozenset({doc("excluded.md")})),
    )


class TestPathCanonicalization:
    """A filter must see the file an operation touches, not the alias it named."""

    @pytest.fixture()
    def workspace(self, tmp_path: Path) -> Path:
        root = tmp_path / "workspace"
        (root / "sub").mkdir(parents=True)
        (root / "excluded.md").write_text("secret")
        (root / "allowed.md").write_text("ok")
        (root / "alias.md").symlink_to(root / "excluded.md")
        (tmp_path / "outside").mkdir()
        (tmp_path / "outside" / "secret.md").write_text("secret")
        (root / "escape").symlink_to(tmp_path / "outside", target_is_directory=True)

        return root

    @pytest.mark.parametrize(
        ("path", "resolved"),
        [
            ("~/excluded.md", None),
            ("~/sub/../excluded.md", None),
            ("~/alias.md", None),
            ("~/sub/../allowed.md", "allowed.md"),
        ],
    )
    def test_an_alias_cannot_reach_a_filtered_document(
        self, workspace: Path, path: str, resolved: str | None
    ) -> None:
        found = resolve_accessible_file((_scoped_home(workspace),), path)

        assert (found and found[1]) == resolved

    @pytest.mark.parametrize("path", ["~/sub/..", "~/../outside", "~/escape"])
    async def test_a_listing_cannot_escape_its_filter_or_workspace(
        self, workspace: Path, path: str
    ) -> None:
        tool = list_documents(mounts_of((_scoped_home(workspace),)))

        with pytest.raises(Retry, match="does not exist"):
            await tool(path=path, patterns=["*.md"], max_depth=None)


class TestListDocuments:
    """Tests for list_documents (listing and pattern matching)."""

    @pytest.mark.parametrize(
        ("path", "message"),
        [
            ("~/missing", "Directory '~/missing' does not exist"),
            ("@nope/x", "This tool addresses ~"),
        ],
    )
    async def test_an_unlistable_directory_is_a_retry(
        self, tmp_path: Path, path: str, message: str
    ) -> None:
        with pytest.raises(Retry, match=message):
            await list_documents(_home(tmp_path))(path=path)

    async def test_a_subdirectory_scopes_the_listing_and_folds_traversal(
        self, tmp_path: Path
    ) -> None:
        for name in ("sub", "docs"):
            (tmp_path / name).mkdir()
            (tmp_path / name / "a.md").write_text("hello")

        (tmp_path / "top.md").write_text("top")
        tool = list_documents(_home(tmp_path))

        scoped = (await tool(path="~/sub/../docs")).metadata

        assert [entry.file_path for entry in scoped] == ["~/docs/a.md"]

    @pytest.mark.parametrize(
        ("hidden_dir", "shown", "revealed"),
        [
            ("doc.assets", {"doc.md", "doc.assets"}, "doc.assets/img.png"),
            ("__pycache__", {"doc.md"}, "__pycache__"),
        ],
    )
    async def test_ignored_entries_are_hidden_unless_included(
        self, tmp_path: Path, hidden_dir: str, shown: set[str], revealed: str
    ) -> None:
        (tmp_path / "doc.md").write_text("text")
        (tmp_path / hidden_dir).mkdir()
        (tmp_path / hidden_dir / "img.png").write_bytes(b"\x89PNG")
        tool = list_documents(_bare(tmp_path))

        hidden = await tool(max_depth=None)
        included = await tool(max_depth=None, include_ignored=True)

        assert {r.file_path for r in hidden.metadata} == shown
        assert revealed in {r.file_path for r in included.metadata}

    async def test_an_empty_listing_names_its_scope_and_what_it_hid(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "doc.assets").mkdir()
        (tmp_path / "doc.assets" / "img.png").write_bytes(b"\x89PNG")
        (tmp_path / "__pycache__").mkdir()
        (tmp_path / "__pycache__" / "a.pyc").write_bytes(b"x")
        (tmp_path / "__pycache__" / "b.pyc").write_bytes(b"x")
        tool = list_documents(_home(tmp_path))

        assets = await tool(path="~/doc.assets", max_depth=None)
        cache = await tool(path="~/__pycache__", max_depth=None)

        assert assets.metadata == []
        assert assets.return_value == (
            "(no documents under '~/doc.assets')\n\n[1 hidden entry (`.assets` "
            "contents and common build/vendor directories), pass "
            "include_ignored=True to reveal them]"
        )
        assert "2 hidden entries" in cache.return_value


class TestReadDocument:
    """Tests for read_document (line-range reads with line numbers)."""

    @pytest.mark.parametrize(
        ("read", "start", "end", "content"),
        [
            (DocumentRead("doc.md"), 1, 5, "line1\nline2\nline3\nline4\nline5"),
            (DocumentRead("doc.md", offset=2, limit=3), 2, 4, "line2\nline3\nline4"),
            (DocumentRead("doc.md", offset=4), 4, 5, "line4\nline5"),
        ],
        ids=["whole", "range", "offset"],
    )
    async def test_reads_the_window_asked_for(
        self, tmp_path: Path, read: DocumentRead, start: int, end: int, content: str
    ) -> None:
        (tmp_path / "doc.md").write_text("\n".join(f"line{i}" for i in range(1, 6)))

        out = await read_document(_bare(tmp_path))([read])
        result = single(out.metadata)

        assert isinstance(result, DocumentRange)
        assert (result.start_line, result.end_line, result.total_lines) == (
            start,
            end,
            5,
        )
        assert result.content == content
        assert result.content_hash
        assert f"{start}: line{start}" in out.return_value

    async def test_reads_a_file_named_with_a_decomposed_path(
        self, tmp_path: Path
    ) -> None:
        # The file is stored precomposed but a model can only emit the decomposed
        # spelling of a path it was shown. Escapes, not literals, so the two
        # spellings are not both normalised by this file's own encoding.
        (tmp_path / "S\u00dcVOA.md").write_text("content here")

        out = await read_document(_bare(tmp_path))([DocumentRead("SU\u0308VOA.md")])

        assert single(out.metadata).content == "content here"

    @pytest.fixture()
    def workspace(self, tmp_path: Path) -> Path:
        """Every file the unreadable-path cases name."""
        (tmp_path / "report.docx").write_bytes(BINARY)
        (tmp_path / "report.md").write_text("extracted text")
        (tmp_path / "lab.xlsx").write_bytes(BINARY)
        (tmp_path / "lab.md").write_text("extracted text")
        (tmp_path / "blob.bin").write_bytes(b"\x89PNG\r\n\x1a\n\xec\xec\xff\xfe")
        (tmp_path / "reports").mkdir()

        for suffix in VISION_MEDIA_TYPES:
            (tmp_path / f"scan{suffix}").write_text("text wearing a binary extension")

        return tmp_path

    @pytest.mark.parametrize(
        ("path", "message"),
        [
            ("missing.md", "not found"),
            ("../../../etc/passwd", "not found"),
            ("@unknown/doc.md", "not found"),
            ("reports/", "is a directory.*list_documents"),
            ("blob.bin", "not text"),
            ("report.docx", "report.md"),
            ("lab.xlsx", r"query_table.*'lab\.md'"),
            *(
                (f"scan{suffix}", "read_binary_document")
                for suffix in sorted(VISION_MEDIA_TYPES)
            ),
        ],
    )
    async def test_a_document_that_cannot_be_read_as_text_is_a_retry(
        self, workspace: Path, path: str, message: str
    ) -> None:
        with pytest.raises(Retry, match=message):
            await read_document(_bare(workspace))([DocumentRead(path)])

    async def test_a_read_points_a_table_at_query_table(self, tmp_path: Path) -> None:
        # An uploaded spreadsheet is only ever addressed by its markdown
        # projection, so the hint follows the original and not the suffix asked for.
        (tmp_path / "lab.xlsx").write_bytes(BINARY)
        (tmp_path / "lab.md").write_text("| a | b |\n|---|---|\n| 1 | 2 |")
        (tmp_path / "sales.csv").write_text("region,amount\nEU,100")
        (tmp_path / "notes.md").write_text("prose")
        tool = read_document(_home(tmp_path))

        projection = (await tool([DocumentRead("~/lab.md")])).return_value
        tabular = (await tool([DocumentRead("~/sales.csv")])).return_value
        prose = (await tool([DocumentRead("~/notes.md")])).return_value

        assert "query_table" in projection
        assert "'~/lab.xlsx'" in projection
        assert "query_table" in tabular
        assert "query_table" not in prose


class TestReadBinaryDocument:
    async def test_an_unshowable_original_names_its_sidecar_in_its_own_scope(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "report.docx").write_bytes(BINARY)
        tool = read_binary_document(
            mounts_of((SearchPath.of(path=tmp_path, target="@team"),))
        )

        with pytest.raises(Retry, match=r"@team/report\.md"):
            await tool([BinaryRead("@team/report.docx")])

    async def test_native_pdfs_do_not_spend_the_image_cap(self, tmp_path: Path) -> None:
        for name in ("a.pdf", "b.pdf"):
            (tmp_path / name).write_bytes(b"content")

        frames = [Image.new("RGB", (8, 8), (index * 80, 0, 0)) for index in range(3)]
        frames[0].save(
            tmp_path / "c.gif", save_all=True, append_images=frames[1:], duration=100
        )
        tool = read_binary_document(
            _bare(tmp_path),
            executor=None,
            binary_content_mode=BinaryContentMode.NATIVE,
            max_images=1,
        )

        result = await tool(
            [BinaryRead("a.pdf"), BinaryRead("b.pdf"), BinaryRead("c.gif")]
        )

        files = unpack(result).files
        assert [a.media_type for a in files if isinstance(a, BinaryContent)] == [
            "application/pdf",
            "application/pdf",
            "image/png",
        ]


class TestServeBatch:
    """The list-first contract every batched tool is built on."""

    @staticmethod
    async def _serve(item: str, share: BatchShare) -> ToolReturn[str]:
        """Refuse a `bad` item."""
        if item.startswith("bad"):
            raise Retry(f"{item} refused")

        return tool_return(f"{item}:{share.of(10)}")

    async def test_dedupes_and_keeps_request_order_with_split_budgets(self) -> None:
        last_done = asyncio.Event()

        async def serve(item: str, share: BatchShare) -> ToolReturn[str]:
            """Finish the first item only after the last one did."""
            if share.index == 0:
                await last_done.wait()

            out = await self._serve(item, share)

            if share.index == share.count - 1:
                last_done.set()

            return out

        out = await serve_batch(["c", "a", "c", "b"], serve, key=str, concurrency=3)

        assert out.metadata == ("c:4", "a:3", "b:3")
        assert out.return_value == "==> c <==\nc:4\n\n==> a <==\na:3\n\n==> b <==\nb:3"

    async def test_a_failed_item_is_reported_in_place(self) -> None:
        out = await serve_batch(["bad", "a"], self._serve, key=str)

        assert out.metadata == (ItemFailure(item="bad", reason="bad refused"), "a:5")
        assert "==> bad <==\nfailed: bad refused" in unpack(out).text
        assert TypeAdapter(BatchData[str]).dump_python(out.metadata, mode="json") == [
            {"item": "bad", "reason": "bad refused", "kind": "failure"},
            "a:5",
        ]

    async def test_only_a_batch_with_every_item_failed_is_refused(self) -> None:
        with pytest.raises(Retry, match="^bad: bad refused$"):
            await serve_batch(["bad"], self._serve, key=str)

        with pytest.raises(Retry, match="Every item failed:\n- bad1: bad1"):
            await serve_batch(["bad1", "bad2"], self._serve, key=str)


type Mutation = Callable[[tuple[SearchPath, ...], Commit], Awaitable[ToolReturn[str]]]
"""One call of a mutation tool against a workspace holding every file the cases name."""

_EDITS = (TextEdit("hello", "goodbye", True), TextEdit("a", "b"))


async def _unreachable(_changeset: Changeset[str]) -> str:
    """Every commit a rejection test hands its tool: it must not run."""
    raise AssertionError("commit must not run when the call is rejected")


async def _echo_write(changeset: Changeset[str]) -> str:
    """A commit that names the document it was given."""
    return f"wrote {changeset.locations[0]}"


def _edit(paths: tuple[SearchPath, ...], commit: Commit) -> Awaitable[ToolReturn[str]]:
    return edit_document(paths, commit=commit)(
        "doc.md", list(_EDITS), expected_hash="h"
    )


def _write(paths: tuple[SearchPath, ...], commit: Commit) -> Awaitable[ToolReturn[str]]:
    return write_document(paths, commit=commit)(
        "doc.md", "content", mode="append", expected_hash="h"
    )


def _move(paths: tuple[SearchPath, ...], commit: Commit) -> Awaitable[ToolReturn[str]]:
    return move_documents(paths, commit=commit)(
        [DocumentMove("old.md", "notes/new.md"), DocumentMove("images", "notes")]
    )


def _delete(
    paths: tuple[SearchPath, ...], commit: Commit
) -> Awaitable[ToolReturn[str]]:
    return delete_documents(paths, commit=commit)(["doc.md", "state.json"])


_MUTATIONS = [
    pytest.param(_edit, Changeset((Edit("doc.md", _EDITS, "h"),)), id="edit"),
    pytest.param(
        _write, Changeset((Write("doc.md", "content", "append", "h"),)), id="write"
    ),
    pytest.param(
        _move,
        Changeset((Move("old.md", "notes/new.md"), Move("images", "notes"))),
        id="move",
    ),
    pytest.param(
        _delete, Changeset((Delete("doc.md"), Delete("state.json"))), id="delete"
    ),
]


class TestMutationTools:
    """Each tool resolves and access-checks its paths, then hands one changeset to its commit.

    The algorithms live in the changeset gateway and are covered there.
    """

    @pytest.fixture()
    def paths(self, tmp_path: Path) -> tuple[SearchPath, ...]:
        for name in ("doc.md", "old.md", "state.json"):
            (tmp_path / name).write_text("x")

        (tmp_path / "images").mkdir()

        return (SearchPath.of(tmp_path),)

    @pytest.mark.parametrize(("call", "expected"), _MUTATIONS)
    async def test_hands_one_changeset_to_the_commit(
        self, paths: tuple[SearchPath, ...], call: Mutation, expected: Changeset[str]
    ) -> None:
        staged: list[Changeset[str]] = []

        async def commit(changeset: Changeset[str]) -> str:
            staged.append(changeset)

            return "done"

        assert (await call(paths, commit)).metadata == "done"
        assert staged == [expected]

    @pytest.mark.parametrize(("call", "expected"), _MUTATIONS)
    async def test_translates_a_refused_commit(
        self, paths: tuple[SearchPath, ...], call: Mutation, expected: Changeset[str]
    ) -> None:
        async def commit(_changeset: Changeset[str]) -> str:
            raise ChangeRefused(status_code=409, detail="Destination already exists")

        with pytest.raises(Retry, match="Destination already exists"):
            await call(paths, commit)

    @pytest.mark.parametrize(
        ("call", "message"),
        [
            (
                lambda paths: edit_document(paths, commit=_unreachable)(
                    "../escape.md", [TextEdit("a", "b")]
                ),
                "not accessible",
            ),
            (
                lambda paths: move_documents(paths, commit=_unreachable)(
                    [DocumentMove("old.md", "new.md"), DocumentMove("gone.md", "b.md")]
                ),
                "'gone.md' not found",
            ),
            (
                lambda paths: delete_documents(paths, commit=_unreachable)(["images"]),
                "is a directory",
            ),
            (
                lambda paths: delete_documents(paths, commit=_unreachable)(["gone.md"]),
                "not found",
            ),
        ],
        ids=["edit-outside", "move-missing", "delete-directory", "delete-missing"],
    )
    async def test_refuses_a_path_it_cannot_resolve_before_committing(
        self,
        paths: tuple[SearchPath, ...],
        call: Callable[[tuple[SearchPath, ...]], Awaitable[ToolReturn[str]]],
        message: str,
    ) -> None:
        with pytest.raises(Retry, match=message):
            await call(paths)

    @pytest.mark.parametrize(
        ("name", "content", "message"),
        [
            (
                "t.csv",
                "date,load,ew\n2023-01-01,1,2\n,mean: 3\n",
                "line 3 has 2 fields",
            ),
            ("t.tsv", "a\tb\tc\n1\t2\t3\t4\n", "line 2 has 4 fields"),
            ("t.csv", "date,load\n2023-01-01,1\n2023-01-02,None\n", "'None'"),
            ("sheet.xlsx", "a,b", "binary format"),
        ],
        ids=["wrong-width", "tab-separated", "none-cell", "binary"],
    )
    async def test_write_refuses_what_would_corrupt_a_document(
        self, tmp_path: Path, name: str, content: str, message: str
    ) -> None:
        tool = write_document((SearchPath.of(tmp_path),), commit=_unreachable)

        with pytest.raises(Retry, match=message):
            await tool(name, content)

    @pytest.mark.parametrize(
        ("name", "content"),
        [
            ("t.csv", "date,load,ew\n2023-01-01,1,2\n,,3\n"),
            # Comma-separated by the name it was given, so one column.
            ("t.csv", "a;b;c\n1;2;3;4\n"),
            ("t.csv", "date,load\n2023-01-01,N/A\n"),
            ("notes.md", "a line\nand, another\n"),
            ("data.txt", "content"),
            ("page.html", "x"),
            ("diagram.svg", "x"),
        ],
    )
    async def test_write_accepts_any_text_document(
        self, tmp_path: Path, name: str, content: str
    ) -> None:
        tool = write_document((SearchPath.of(tmp_path),), commit=_echo_write)

        assert (await tool(name, content)).metadata == f"wrote {name}"


@pytest.mark.parametrize("excluded", [doc("docs/deep/secret.md"), folder("docs/deep")])
async def test_a_directory_move_cannot_carry_excluded_entries(
    data_dir: Path, excluded: FilterEntry
) -> None:
    store = HostGrant().workspace(UserScope("u"))
    secret = store.path / "docs" / "deep" / "secret.md"
    secret.parent.mkdir(parents=True)
    secret.write_text("secret")
    hidden = DocumentFilter(excluded=frozenset({excluded}))
    gateway = Gateway((Root.of(store, hidden),))

    with pytest.raises(ChangeRefused, match="contains inaccessible entries") as error:
        _ = await gateway.plan(Changeset((Move("~/docs", "~/archive"),)))

    assert "secret" not in str(error.value.detail)


class TestJq:
    """Tests for jq."""

    async def test_a_filter_selects_values_and_none_reports_the_shape(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "item.json").write_text(json.dumps({"title": "Hello", "n": 42}))
        tool = jq(_bare(tmp_path))

        selected = await tool(["item.json"], ".title")
        shape = await tool(["item.json"])

        assert single(selected.metadata).values == ("Hello",)
        # The cheap first call: keys and types, never the document itself.
        assert single(shape.metadata).values == ({"title": "string", "n": "number"},)
        assert "Hello" not in shape.return_value

    @pytest.mark.parametrize(
        ("path", "expression", "message"),
        [
            ("notes.md", ".", "not a JSON document"),
            ("item.json", "invalid [[[", None),
            ("broken.json", ".", "jq failed"),
            ("missing.json", ".", "not found"),
            ("../etc/passwd", ".", "not found"),
        ],
    )
    async def test_a_call_that_cannot_run_is_a_retry(
        self, tmp_path: Path, path: str, expression: str, message: str | None
    ) -> None:
        (tmp_path / "notes.md").write_text("# notes")
        (tmp_path / "item.json").write_text(json.dumps({"x": 1}))
        (tmp_path / "broken.json").write_text("{not json")

        with pytest.raises(Retry, match=message):
            await jq(_bare(tmp_path))([path], expression)


class TestGrepSearch:
    """Tests for grep against real files on disk."""

    @staticmethod
    def _filenames(output: list[GrepMatch]) -> set[str]:
        return {m.file_path for m in output}

    async def test_a_glob_naming_a_binary_format_is_refused(
        self, tmp_path: Path
    ) -> None:
        # ripgrep skips a binary file without a word, so this search would
        # answer "(no matches)", which a run takes for proof the term is absent.
        with pytest.raises(Retry, match="query_table"):
            await grep(_bare(tmp_path))("TKN", glob="*.xlsx")

    @pytest.mark.parametrize(
        ("glob", "found"),
        [
            (None, {"doc.md", "raw.xml"}),
            ("*.{xml,md}", {"doc.md", "raw.xml"}),
            ("*.xml", {"doc.xml", "raw.xml"}),
        ],
    )
    async def test_an_original_is_dropped_when_its_description_matches_too(
        self, tmp_path: Path, glob: str | None, found: set[str]
    ) -> None:
        (tmp_path / "doc.xml").write_text("<a>needle</a>\n")
        (tmp_path / "doc.md").write_text("```xml\n<a>needle</a>\n```\n")
        (tmp_path / "raw.xml").write_text("<a>needle</a>\n")

        result = await grep(_bare(tmp_path))("needle", glob=glob)

        assert self._filenames(result.metadata) == found

    async def test_assets_payload_is_hidden_unless_ignored_are_included(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "doc.assets").mkdir()
        (tmp_path / "doc.assets" / "fig1.txt").write_text("needle\n")
        tool = grep(_bare(tmp_path))

        hidden = await tool("needle")
        revealed = await tool("needle", include_ignored=True)

        assert hidden.metadata == []
        assert "1 hidden entry" in hidden.return_value
        assert self._filenames(revealed.metadata) == {"doc.assets/fig1.txt"}

    async def test_a_parent_glob_cannot_search_outside_the_workspace(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "workspace").mkdir()
        (tmp_path / "secret.md").write_text("needle\n")

        result = await grep(_home(tmp_path / "workspace"))("needle", glob="../*.md")

        assert result.metadata == []


type PythonFactory = Callable[..., ToolFunc]
"""`run_python` with the pool and limits of the test bound."""


def _recording_python_tool(
    python: PythonFactory, workspace: Path
) -> tuple[ToolFunc, list[Changeset[str]]]:
    """Mount *workspace* writable and return what each run stages."""
    staged: list[Changeset[str]] = []

    async def commit(changes: Changeset[str]) -> ChangesetOutcome:
        staged.append(changes)

        return PendingChanges("staged-id", ChangesetSummary(), 0)

    scoped = SearchPath.of(path=workspace, target="~", implied=True)
    configured = python((scoped,), writable=(scoped,), commit=commit)

    return configured, staged


def _home_python(python: PythonFactory, workspace: Path) -> ToolFunc:
    """The tool with *workspace* mounted read-only."""
    return python((SearchPath.of(path=workspace, target="~", implied=True),))


class TestRunPython:
    """Tests for run_python."""

    @pytest.fixture()
    async def python(self) -> AsyncIterator[PythonFactory]:
        async with AsyncMonty(min_processes=1) as pool:
            yield partial(run_python, pool=lambda: pool, changeset_limits=LIMITS)

    async def test_reports_the_value_and_what_the_program_printed(
        self, python: PythonFactory
    ) -> None:
        result = await python()("import math\nprint('working')\nmath.factorial(5)")
        bare = await python()("total = 1 + 1")

        assert result.metadata == PythonResult(result="120", stdout="working")
        assert result.return_value == "working\nResult: 120"
        assert bare.metadata == PythonResult()
        assert "no value" in bare.return_value

    async def test_a_long_result_names_the_cut_and_printed_output_stays_whole(
        self, python: PythonFactory
    ) -> None:
        cut = await python()("list(range(5_000))")
        printed = await python()("print('x' * 100_000)")

        assert "998, 999, ...]" in cut.return_value
        assert cut.return_value.endswith(
            "print() it or write it to a file to get all of it]"
        )
        # The spill bounds the text, so the data a client renders keeps it all.
        assert printed.metadata.stdout == "x" * 100_000

    async def test_the_environment_mirrors_a_shell(self, python: PythonFactory) -> None:
        configured = python(
            environ={"HOME": "/workspace/~", "USER": "u", "TMPDIR": "/tmp"}
        )

        result = await configured(
            "import os\n"
            "[os.getenv(k) for k in ('HOME', 'USER', 'PWD', 'TMPDIR', 'LANG')]"
            " + [os.getcwd()]"
        )

        assert result.metadata.result == (
            "['/workspace/~', 'u', '/workspace', '/tmp', 'C.UTF-8', '/workspace']"
        )

    @pytest.mark.parametrize(
        ("arguments", "message"),
        [
            ({"code": "   "}, "program is empty"),
            # Named apart from the empty case, since a model told to "provide
            # one of" what it just provided both of looks for the fault elsewhere.
            ({"code": "1", "script_path": "~/run.py"}, "both given"),
        ],
    )
    async def test_a_call_without_exactly_one_program_is_refused(
        self,
        python: PythonFactory,
        tmp_path: Path,
        arguments: dict[str, str],
        message: str,
    ) -> None:
        with pytest.raises(Retry, match=message):
            await _home_python(python, tmp_path)(**arguments)

    async def test_a_failure_retries_with_traceback_and_prior_output_and_stages_nothing(
        self, python: PythonFactory, tmp_path: Path
    ) -> None:
        workspace_tool, staged = _recording_python_tool(python, tmp_path)

        with pytest.raises(Retry, match="ZeroDivisionError") as exc_info:
            await workspace_tool(
                'from pathlib import Path\nPath("~/out.txt").write_text("new")\n'
                "print('before')\n1 / 0"
            )

        assert "before" in str(exc_info.value)
        assert staged == []

    async def test_a_limit_ends_a_runaway_program_as_a_retry(
        self, python: PythonFactory
    ) -> None:
        bounded = python(limits={"max_recursion_depth": 20})

        with pytest.raises(Retry, match="RecursionError"):
            await bounded("def f():\n    return f()\nf()")

    async def test_an_oversized_document_is_refused_before_it_is_decoded(
        self, python: PythonFactory, tmp_path: Path
    ) -> None:
        # The budget bounds host memory, so a file that cannot fit it must never
        # reach the decoder: a program with the workspace mounted can open every
        # document in it, one after the other.  Read as a binary it would be
        # another error than the budget's, which only a refusal first avoids.
        (tmp_path / "big.txt").write_bytes(b"\x00" * 1000)
        bounded = python(
            (SearchPath.of(path=tmp_path, target="~", implied=True),),
            max_document_chars=200,
        )

        result = await bounded(
            "from pathlib import Path\n"
            "try:\n"
            '    Path("~/big.txt").read_text()\n'
            "except MemoryError as exc:\n"
            "    refusal = str(exc)\n"
            "refusal"
        )

        assert "~/big.txt" in str(result.metadata.result)

    async def test_a_stored_script_reads_the_mount_and_stages_its_writes(
        self, python: PythonFactory, tmp_path: Path
    ) -> None:
        (tmp_path / "script.py").write_text(
            "from pathlib import Path\n"
            'text = Path("~/input.txt").read_text()\n'
            'Path("~/output.txt").write_text(text.upper())\n'
            'Path("~/output.txt").read_text()'
        )
        (tmp_path / "input.txt").write_text("hello")
        workspace_tool, staged = _recording_python_tool(python, tmp_path)

        # An argument a model spelled empty is the one it did not use.
        result = await workspace_tool(code="", script_path="~/script.py")

        assert [changes.operations for changes in staged] == [
            (Write("~/output.txt", "HELLO", "create"),)
        ]
        assert result.metadata.result == "'HELLO'"
        assert result.metadata.changeset == PendingChanges(
            "staged-id", ChangesetSummary(), 0
        )
        assert "changeset_id='staged-id'" in result.return_value
        assert not (tmp_path / "output.txt").exists()

    async def test_a_stored_script_is_reloaded_after_an_edit(
        self, python: PythonFactory, tmp_path: Path
    ) -> None:
        script = tmp_path / "script.py"
        workspace_tool = _home_python(python, tmp_path)

        script.write_text("1 + 1")
        first = await workspace_tool(script_path="~/script.py")
        script.write_text("1 + 2")
        second = await workspace_tool(script_path="~/script.py")

        assert (first.metadata.result, second.metadata.result) == ("2", "3")

    async def test_a_program_discovers_a_document_it_was_never_told_about(
        self, python: PythonFactory, tmp_path: Path
    ) -> None:
        (tmp_path / "notes").mkdir()
        (tmp_path / "notes" / "one.md").write_text("alpha")
        (tmp_path / "notes" / "two.md").write_text("beta")

        result = await _home_python(python, tmp_path)(
            "from pathlib import Path\n"
            'sorted(p.read_text() for p in Path("~/notes").iterdir())'
        )

        assert result.metadata.result == "['alpha', 'beta']"

    async def test_renames_and_removals_stage_through_normal_path_calls(
        self, python: PythonFactory, tmp_path: Path
    ) -> None:
        (tmp_path / "inbox").mkdir()

        for name in ("a", "b"):
            (tmp_path / "inbox" / f"{name}.md").write_text(name)

        (tmp_path / "stale.md").write_text("old")
        workspace_tool, staged = _recording_python_tool(python, tmp_path)

        result = await workspace_tool(
            "from pathlib import Path\n"
            'Path("~/archive").mkdir()\n'
            'for p in Path("~/inbox").iterdir():\n'
            '    p.rename(Path("~/archive") / p.name)\n'
            'Path("~/inbox").rmdir()\n'
            'Path("~/stale.md").unlink()\n'
            '[p.name for p in Path("~").iterdir()]'
        )

        assert result.metadata.result == "['archive']"
        (changes,) = staged
        assert [type(op) for op in changes.operations] == [Move, Move, Delete, Delete]
        assert changes.locations == (
            "~/inbox/a.md",
            "~/archive/a.md",
            "~/inbox/b.md",
            "~/archive/b.md",
            "~/inbox",
            "~/stale.md",
        )
        assert sorted(p.name for p in tmp_path.iterdir()) == ["inbox", "stale.md"]

    async def test_every_change_is_refused_without_a_writable_span(
        self, python: PythonFactory, tmp_path: Path
    ) -> None:
        with pytest.raises(Retry, match="cannot be changed"):
            await _home_python(python, tmp_path)(
                'from pathlib import Path\nPath("~/state.json").write_text("{}")'
            )

        assert not (tmp_path / "state.json").exists()

    async def test_a_program_that_changes_nothing_stages_nothing(
        self, python: PythonFactory, tmp_path: Path
    ) -> None:
        (tmp_path / "notes.md").write_text("same")
        workspace_tool, staged = _recording_python_tool(python, tmp_path)

        result = await workspace_tool(
            'text = open("~/notes.md").read()\nopen("~/notes.md", "w").write(text)'
        )

        assert staged == []
        assert result.metadata.changeset is None
