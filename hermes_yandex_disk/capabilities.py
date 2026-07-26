"""Runtime capability probing.

Some Yandex Disk endpoints are gated to certain applications rather than to a
token scope you can request. ``/resources/search`` is one: a token minted by an
app without that permission gets a 403, and no scope on a self-registered app
unlocks it. There is no metadata that reveals the grant up front, so the only
reliable signal is to make the call once and look at the status.

:func:`search_available` does exactly that — a single, cheap, never-raising
probe, cached per token for the process. ``register(ctx)`` uses it to decide
whether the search tool is offered at all, so a token that cannot search never
sees the tool advertised. The probe runs once at load; ``check_fn`` on the
registered tool stays the cheap credential check.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from .client import YandexDiskClient, YandexDiskError

logger = logging.getLogger(__name__)

#: Statuses that mean "this app may not use the endpoint", as opposed to a
#: transient failure. Anything else (including a 400 for a probe query the
#: server dislikes) means the call was authorised.
_DENIED = frozenset({401, 403})

#: token fingerprint -> capability. Keyed by token so swapping tokens re-probes.
_search_cache: dict[int, bool] = {}


def _probe(client: YandexDiskClient) -> bool:
    """True if the token may call search; False if denied.

    A denied call (401/403) is a verdict. Any other failure — a 5xx, a rate
    limit, a transport error — is inconclusive and re-raised, so the caller
    treats it as "unavailable for now" without caching it.
    """
    try:
        client.search("a", limit=1)
        return True
    except YandexDiskError as exc:
        if exc.status in _DENIED:
            return False
        raise


def search_available(
    token: str,
    client_factory: Callable[[], YandexDiskClient],
    *,
    use_cache: bool = True,
) -> bool:
    """Whether ``token`` may use the search endpoint, cached per token.

    ``client_factory`` builds the client to probe with (injectable for tests).
    A definitive yes/no is cached; an inconclusive probe (network error, 5xx) is
    not, so the next load tries again rather than hiding search forever.
    """
    key = hash(token)
    if use_cache and key in _search_cache:
        return _search_cache[key]
    try:
        with client_factory() as client:
            available = _probe(client)
    except Exception as exc:
        logger.debug("Search capability probe was inconclusive: %s", exc)
        return False
    _search_cache[key] = available
    return available


def _reset_cache() -> None:
    """Test helper: forget every probed capability."""
    _search_cache.clear()
