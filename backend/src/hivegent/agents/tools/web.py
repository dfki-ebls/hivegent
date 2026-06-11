"""Web-oriented agent tool registrations.

The tool classes are settings-free; this module is where the application
settings are applied to their instance fields.  The web tools are only
registered while a web host policy is configured
(``HIVEGENT_SECURITY__WEB_ALLOW_HOSTS`` / ``WEB_DENY_HOSTS``, which
default to the Wikimedia projects): without one, letting the model
dereference arbitrary websites is unsafe, and a search whose results
could never be opened is pointless — so both tools are hidden
altogether.
"""

from pydantic_ai import FunctionToolset

from ...config import settings
from ...security import web_url_policy
from ...tools import WebFetch, WebSearch
from ...tools.pydantic_ai import register_agent_tools
from ..common import UserDeps

__all__ = ["web_toolset"]

_policy = web_url_policy()


def _web_search(_deps: UserDeps) -> WebSearch:
    return WebSearch(
        backend=settings.network.websearch_backend,
        region=settings.network.websearch_region,
        policy=_policy,
    )


def _web_fetch(_deps: UserDeps) -> WebFetch:
    network = settings.network
    return WebFetch(
        timeout_seconds=network.webfetch_timeout_seconds,
        max_response_bytes=network.webfetch_max_response_bytes,
        max_chars=network.webfetch_max_chars,
        max_redirects=network.webfetch_max_redirects,
        policy=_policy,
    )


web_toolset: FunctionToolset[UserDeps] = FunctionToolset(defer_loading=False)

if _policy.restricts_hosts:
    register_agent_tools(
        web_toolset,
        UserDeps,
        [
            _web_search,
            _web_fetch,
        ],
    )
