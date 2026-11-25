"""The external sources the operator configures, mirrored to disk and indexed.

Each entry of ``settings.sources`` is reached through a
`shared.connectors.Connector`, mirrored to
``<data_dir>/sources/<id>/`` by :mod:`.sync`, and mounted read-only at
``/mnt/<id>`` for its audience (:class:`~hivegent.store.SourceScope`), so the
mirror is searched, cited, and previewed like any workspace.  A source is
read-only by design: no connector writes to it, and only its sync changes the mirror.
"""
