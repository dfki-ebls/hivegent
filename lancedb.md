You are working in the cbrkit repository (/Users/mlenz/Developer/wi2trier/cbrkit).

Recent refactorings made the SQLAlchemy/pgvector indexable backend the reference
design for storage + retrieval, but the LanceDB backend was left on the old API
and never brought along. Your job is to bring the LanceDB backend (storage AND
retriever) up to parity with that reference design, so it satisfies the same
protocols and is a drop-in for an async, Filter-based stack.

## Reference files (the target design)
- src/cbrkit/typing.py — the protocols: `IndexableFunc`, `FilterableIndexableFunc`,
  `AsyncIndexableFunc`, `AsyncFilterableIndexableFunc`.
- src/cbrkit/filter.py — backend-agnostic Filter AST (`Eq/In/Like/And/Or/Not/Raw`).
  Its module docstring already promises "lancedb → SQL string" compilation that
  does not yet exist.
- src/cbrkit/indexable/sqlalchemy.py — `sqlalchemy_async` (async-first, generic
  value type `V`, `row_factory`/`row_dump`, `compile_filter`, `stream_rows`) and its
  sync facade `sqlalchemy`.
- src/cbrkit/indexable/pgvector.py — `pgvector_async`/`pgvector`, adds system
  columns + `reembed_all`.
- src/cbrkit/retrieval/indexable/pgvector.py — `pgvector_async`/`pgvector`
  retrievers (Filter-based `where`, async + sync facade, RRF hybrid).
- src/cbrkit/indexable/_common.py — reusable `_sql_literal`, `_sql_in_clause`,
  `_compute_index_diff`, `_normalize_patch_keys`.

## Files to change
- src/cbrkit/indexable/lancedb.py (storage)
- src/cbrkit/retrieval/indexable/lancedb.py (retriever)
- plus exports in the respective __init__.py files and tests/test_indexable.py.

## Gap overview — what LanceDB is missing vs sqlalchemy_async / pgvector retriever

STORAGE (indexable/lancedb.py):
1. Wrong protocol. It implements only `IndexableFunc[Casebase[K, str], Collection[K]]`
   — sync, and value type hard-wired to `str` with an out-of-band `metadata=` kwarg
   on every write. The reference implements `FilterableIndexableFunc` (sync) and
   `AsyncFilterableIndexableFunc` (async) with a generic `V = Mapping[str, Any]`
   exchanged as full rows via `row_factory`/`row_dump`. LanceDB is columnar and can
   hold arbitrary columns, so adopt the same generic full-row model and retire the
   `metadata=` side-channel.
2. No structured Filter. `keys_where`/`delete_where`/`replace_where` take a native
   `where: str`, so they do NOT match the `FilterableIndexableFunc` signature
   (`where: Filter`). Add a `compile_filter(Filter) -> str` that lowers the AST to a
   LanceDB SQL predicate string (reuse `_sql_literal`/`_sql_in_clause`; handle
   `Like.escape`; `Raw.sql` passes through verbatim). Switch the three methods to
   accept `Filter`.
3. No async variant. There is no `lancedb_async`. LanceDB ships an async client
   (`lancedb.connect_async` → AsyncConnection/AsyncTable). Mirror the pgvector split:
   an async-first `lancedb_async` implementing `AsyncFilterableIndexableFunc`, and a
   thin sync `lancedb` facade over it (see how `sqlalchemy`/`pgvector` wrap their
   async classes with `run_coroutine`). Add async `get_index` / `has_index`.
4. No `reembed_all`. The docstring still says "drop the table when changing models."
   Add `reembed_all(batch_size)` that pages rows, re-embeds the text/value column,
   and updates the vector column in place (mirror pgvector_async.reembed_all).
5. No `stream_rows` paging helper (sqlalchemy_async has one).
6. Naming drift: lancedb uses `value_column`/`vector_column`; the reference uses
   `text_column`/`pgvector_column`. Align names where it doesn't break the columnar
   model (at least document the mapping).

RETRIEVER (retrieval/indexable/lancedb.py):
1. Sync only. It implements `RetrieverFunc`; there is no `lancedb_async`
   (`AsyncRetrieverFunc`), so it can't be driven by
   `cbrkit.retrieval.apply_query_indexed_async`. Add the async retriever + a sync
   facade, exactly like pgvector.
2. `where: str | None` must become `where: Filter | None`, compiled via the new
   storage `compile_filter`. This is the key parity item: downstream code builds
   `cbrkit.filter.Filter` values and expects every retriever to accept them.
3. `search_type` should default to `None` and resolve to the storage's `index_type`
   at call time (pgvector does this), instead of hard-defaulting to `"dense"`.
4. Hybrid fusion: pgvector exposes RRF knobs (`RrfMixin`: `rrf_k`, `rrf_weights`,
   `hybrid_oversample`). LanceDB does fusion internally and returns
   `_relevance_score`. Don't force RRF onto LanceDB if its native hybrid is better —
   but document the difference, and confirm the score normalization path matches.

## Constraints / conventions
- Match the existing code style: dataclasses with `slots=True`, Google-style
  docstrings, doctests where they already appear, `__all__` on every module,
  `@override` on protocol methods, keyword-only where the reference uses it.
- The sync/async pair MUST get a field-parity test mirroring
  `test_sqlalchemy_storage_sync_async_field_parity` /
  `test_pgvector_retriever_sync_async_field_parity` in tests/test_indexable.py.
- Add Filter-compilation tests (every AST node → expected LanceDB SQL) and a
  round-trip write→filter→search test, gated by `pytest.importorskip("lancedb")`.
- LanceDB is an optional dependency: keep imports inside the module so the
  `optional_dependencies()` guards in the __init__ files still work.
- Preserve backward-incompatible API changes cleanly (this project prefers breaking
  changes over compat shims) — but call them out in your summary.

## Deliverables
1. First, WITHOUT writing code, produce a short written gap analysis confirming or
   correcting the overview above after reading the actual files (note anything I got
   wrong, e.g. if LanceDB's async client can't support a needed operation).
2. Then implement storage parity (Filter compiler → async class → sync facade →
   reembed_all), then retriever parity (async class + sync facade, Filter `where`).
3. Update __init__.py exports and add the tests.
4. Run the checks and make them pass: `uv run ruff check`, `uv run ty check`,
   `uv run basedpyright --level error`, and `uv run pytest tests/test_indexable.py`.
5. End with a summary of what changed, any API breaks, and any parity item you
   deliberately did NOT port (e.g. RRF) with the reason.

Start by reading the reference files and the two LanceDB files, then give me the
gap analysis before touching any code.
