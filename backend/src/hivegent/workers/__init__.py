"""Running work in separate worker processes.

Both mechanisms run a picklable callable in a separate process, so a native
crash (a pdfium segfault, a torch abort) or a runaway hang takes down only a
worker, never the server.  They differ in process lifetime, and that is the
whole reason there are two:

- **Isolation** (:mod:`.isolation`) — a single-use process per call, killed by a
  wall-clock timeout on expiry or cancellation.  Use it for untrusted or
  hang-prone native work with no warm-up to keep (currently pdfium paging).

- **Pool** (:mod:`.pool`) — a persistent pool that loads its heavy engine once
  and keeps it warm.  Use it for expensive-to-load, reusable stages (conversion,
  chunking); a worker crash rebuilds the pool and retries the task once.

The two are sized independently (``isolation.max_workers`` vs
``compute.worker_processes``), so their process counts add up.  Both spawn with
the ``spawn`` start method and rely on ``hivegent/__main__.py`` being
import-guarded so a worker never re-runs the CLI.  Worker bodies live in
dependency-light leaf modules (e.g. :mod:`.pdf`) with heavy libraries imported
lazily inside the function, keeping worker startup fast.
"""
