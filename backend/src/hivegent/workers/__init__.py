"""Running work in separate worker processes.

Both mechanisms here run a picklable callable in a *separate process*, so a
native crash (a pdfium segfault, a torch abort) or a runaway hang can only take
down a worker, never the server.  They differ in process **lifetime** and
**supervision**, and that difference is the whole reason there are two:

- **Isolation** (:mod:`.isolation`, :func:`~.isolation.run_isolated`) — a
  **single-use** process per call, supervised by a wall-clock timeout that
  *kills* the worker on expiry or cancellation.  For **crash-prone, untrusted,
  possibly-hanging** native calls with no warm-up worth keeping: currently
  pdfium paging (:mod:`.pdf`), whose input is untrusted PDFs.  A fresh process
  per call means a malformed document can neither corrupt the next call nor
  wedge a long-lived worker.

- **Pool** (:mod:`.pool`, :func:`~.pool.run_offloaded`) — a **persistent** pool
  of reused processes that load their heavy engine **once** and keep it warm.
  For **expensive-to-initialize, trusted, reusable** pipeline stages: document
  conversion and chunking, whose models cost seconds to load.  There is no
  per-call kill (a persistent worker holding a warm model is not cheap to
  discard); a crash instead rebuilds the pool and retries the task once.

Choosing between them: reach for **isolation** when the work is untrusted or can
hang and you need a hard timeout and a pristine process each time; reach for the
**pool** when the work reloads an expensive engine and you want that cost paid
once per worker.  The two are sized independently — ``settings.isolation.
max_workers`` caps concurrent single-use workers, ``settings.compute.
worker_processes`` is the persistent pool size — so their process counts add up
and a host must budget for both at once.

Both spawn with the ``spawn`` start method and rely on ``hivegent/__main__.py``
being import-guarded, so a worker never re-runs the CLI on startup.  Worker
bodies live in dependency-light leaf modules (e.g. :mod:`.pdf`): a spawned
worker imports only the module holding its target callable, so keeping that
chain minimal — stdlib-only top-level imports, heavy libraries imported lazily
inside the function — keeps worker startup fast.
"""
