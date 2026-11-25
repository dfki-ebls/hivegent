"""Similarity search over case bases by example, and storing cases into them, with cbrkit.

A case base is a CSV, XLSX, Parquet, JSON array, or JSON Lines file, a folder
of JSON files (one case each, keyed by its path in the folder), or graphs as
BPMN or cbrkit graph JSON, a single file or a folder of them
(`cbrkit.io.detect`).  Its similarity configuration holds the heuristic
decisions of `cbrkit.spec.infer` with their provenance and only lives in
memory.  A sidecar ``<name>.cbrkit.json`` is neither read nor written while the
format of cbrkit's specifications still evolves, so reading a case base writes
nothing.

The submodules open and cache the case bases (:mod:`~hivegent.tools.cases.casebase`),
rank them by example (:mod:`~hivegent.tools.cases.query`), render the hits
(:mod:`~hivegent.tools.cases.render`), and offer the tools that search
(:mod:`~hivegent.tools.cases.search`) and change (:mod:`~hivegent.tools.cases.plan`) them.
"""

from .casebase import (
    EMBEDDER,
    LANGUAGE_MODEL,
    Bind,
    Bindings,
    CaseBase,
    CaseBaseCache,
    Limits,
    open_case_base,
)
from .plan import CasePlanResult, maintain_case_base, retain_cases
from .query import Query, parse_query, rank
from .render import CaseMatch, SimilarCasesResult, match
from .search import similar_cases

__all__ = [
    "EMBEDDER",
    "LANGUAGE_MODEL",
    "Bind",
    "Bindings",
    "CaseBase",
    "CaseBaseCache",
    "CaseMatch",
    "CasePlanResult",
    "Limits",
    "Query",
    "SimilarCasesResult",
    "maintain_case_base",
    "match",
    "open_case_base",
    "parse_query",
    "rank",
    "retain_cases",
    "similar_cases",
]
