"""Built-in MCP tool registrations."""

from importlib import import_module

__all__: list[str] = []

for module_name in ("documents", "mutations", "retrieval"):
    import_module(f"{__name__}.{module_name}")
