"""What one caller may read across workspaces, the stores and the filters narrowing them together."""

from collections.abc import Mapping
from dataclasses import dataclass

from .store import User, Workspace, WorkspaceScope
from .tools.base import SearchPath
from .types import DocumentFilter

__all__ = ["Reach"]


@dataclass(slots=True, frozen=True)
class Reach:
    """Every workspace a :class:`User` sees, each with the filter a run applies to it.

    The one input of every read spanning workspaces, the path tools' roots
    (:meth:`search_paths`), the chunk index (``retrieval.document_memory``),
    and the documents query (``db.documents.accessible_documents``), so a
    store never travels without its filter and an assistant's allowlist is
    never dropped by a forgotten argument.  The stores come from the user
    alone, so a reach can narrow what the user sees but never widen it.

    Attributes:
        user: Whose workspaces it reaches.
        filters: The filter of each scope, an absent one filtering nothing.
    """

    user: User
    filters: Mapping[WorkspaceScope, DocumentFilter]

    @property
    def stores(self) -> tuple[Workspace, ...]:
        """The workspaces reached, as :meth:`User.workspaces` orders them."""
        return self.user.workspaces()

    def filter(self, store: Workspace) -> DocumentFilter | None:
        """The filter applied to *store*, ``None`` for none."""
        return self.filters.get(store.scope)

    def search_paths(self) -> tuple[SearchPath, ...]:
        """The roots a path tool resolves, filters applied, without creating any.

        A mutation makes its own destination downstream and a read of a
        workspace nobody has written to yet finds nothing, so handing a tool
        its roots must not materialise an empty directory for every group the
        caller can reach.
        """
        return tuple(store.search_path(self.filter(store)) for store in self.stores)
