"""Plugins: Python packages an operator installs and enables, trusted like a dependency.

A plugin imports :mod:`.api` alone, and :mod:`.loader` loads the enabled ones.
This package itself imports nothing, so a worker process unpickling a plugin's
converter loads no other plugin.
"""
