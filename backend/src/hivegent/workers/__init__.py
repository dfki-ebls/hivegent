"""Crash-isolated worker processes.

Some native libraries (pdfium, ...) can segfault or abort the whole
interpreter on malformed input.  This package runs such calls in a
throwaway spawned process via :func:`hivegent.workers.isolation.run_isolated`
so a crash kills only the worker, not the server.

Worker bodies live in dependency-light leaf modules (e.g. :mod:`.pdf`): a
spawned worker imports only the module holding its target function, so
keeping that chain minimal — an empty package ``__init__``, stdlib-only
top-level imports, heavy libraries imported lazily inside the function —
keeps startup fast.  This ``__init__`` is intentionally empty for the same
reason.
"""
