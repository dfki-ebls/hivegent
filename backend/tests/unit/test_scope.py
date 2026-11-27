"""Unit tests for the scope grammar (~, @<group>, /srv) and its routing."""

from pathlib import Path

import pytest
from exar.memory.mounts import render, strip

from hivegent.agents.common import UserDeps
from hivegent.server.common import parse_document_scope
from hivegent.store import (
    GlobalScope,
    GroupScope,
    HostGrant,
    User,
    UserScope,
    parse_scope,
    split_scope_path,
)
from hivegent.tmp import tmp_dir
from hivegent.tools.base import SearchPath, resolve_accessible_file
from hivegent.tools.changes import Changeset, Delete
from hivegent.types import DocumentFilter, FilterEntry
from hivegent.workspace.operations import Location, Root, route
from tests.helpers import doc, label_id, owned

SCOPES = (UserScope("u"), GroupScope("team"), GlobalScope())


def _deps(conversation_id: str | None) -> UserDeps:
    return UserDeps(
        User("u"),
        "interactive",
        None if conversation_id is None else owned(conversation_id),
    )


def test_scopes_render_strip_and_split_by_their_prefix() -> None:
    assert UserScope("u").render("reports/q1.md") == "~/reports/q1.md"
    assert GroupScope("team").render("notes.md") == "@team/notes.md"
    assert GlobalScope().render("") == "/srv"

    user, shared = UserScope("u").prefix, GlobalScope().prefix
    assert strip(user, "~/reports") == "reports"
    assert strip(user, "~") == ""
    assert strip(user, "@team/x") is None
    assert strip(shared, "/globals/x") is None

    for raw in ("~", "~/reports/q1.md", "@team", "@team/notes.md", "/srv/a"):
        prefix, local = split_scope_path(raw)
        assert render(prefix, local) == raw

    for raw in ("reports/q1.md", "/tmp/a", "@"):
        with pytest.raises(ValueError):
            split_scope_path(raw)


def test_scope_keys_parse_back() -> None:
    for scope in SCOPES:
        assert parse_scope(scope.key) == scope

    with pytest.raises(ValueError):
        parse_scope("tenant:x")


def test_a_changeset_routes_to_the_roots_it_addresses_and_no_other() -> None:
    roots = tuple(Root.of(HostGrant().workspace(scope)) for scope in SCOPES[:2])
    routed = route(Changeset((Delete("~/notes.md"), Delete("@team/a.md"))), roots)

    assert routed.locations == (
        Location(roots[0], "notes.md"),
        Location(roots[1], "a.md"),
    )

    with pytest.raises(ValueError, match="No accessible workspace"):
        route(Changeset((Delete("@other/notes.md"),)), roots)


def test_each_conversation_resolves_only_its_own_tmp_folder(data_dir: Path) -> None:
    """`/tmp` is the conversation's own folder, outside every workspace."""
    for conversation in ("c1", "c2"):
        folder = tmp_dir(data_dir, owned(conversation))
        folder.mkdir(parents=True)
        (folder / "state.json").write_text(conversation)

    for conversation, other in (("c1", "c2"), ("c2", "c1")):
        paths = _deps(conversation).search_paths()
        sp, local, absolute = resolve_accessible_file(
            paths, "/tmp/state.json"
        ) or pytest.fail("unresolved")
        assert sp.mount.render(local) == "/tmp/state.json"
        assert absolute.read_text() == conversation
        assert (
            resolve_accessible_file(paths, f"/tmp/../{label_id(other)}/state.json")
            is None
        )

    # A caller without a conversation, such as MCP, has no `/tmp` at all.
    assert (
        resolve_accessible_file(_deps(None).search_paths(), "/tmp/state.json") is None
    )


@pytest.mark.usefixtures("folded_case")
def test_a_filter_is_asked_about_the_name_on_disk(tmp_path: Path) -> None:
    """A hidden `secret.md` is not reachable as `SECRET.md` where case folds."""
    (tmp_path / "secret.md").write_text("x")
    hidden = DocumentFilter(excluded=frozenset({doc("secret.md")}))
    paths = (SearchPath.of(tmp_path, "~", implied=True, filter_func=hidden),)

    assert resolve_accessible_file(paths, "~/secret.md") is None
    assert resolve_accessible_file(paths, "~/SECRET.md") is None


@pytest.mark.parametrize(
    ("included", "excluded", "named"),
    [
        ([doc("~/projekte/tabelle.md")], [], "~/projekte/tabelle.md"),
        ([], [doc("~/geheim.md")], "~/geheim.md"),
        ([], [], None),
    ],
    ids=["included", "excluded", "none"],
)
def test_the_selection_of_the_eye_toggle_reaches_the_model(
    included: list[FilterEntry], excluded: list[FilterEntry], named: str | None
) -> None:
    """Nothing else ties a bare "in der Tabelle" to a file, and an unscoped chat spends no prompt on an empty block."""
    user = User("u1")
    relevant, filters = parse_document_scope(included, excluded, user)
    deps = UserDeps(
        user=user,
        mode="interactive",
        document_filters=filters,
        relevant_documents=relevant,
    )

    scope = deps.describe_document_scope()

    assert (scope == "") if named is None else (named in scope)
