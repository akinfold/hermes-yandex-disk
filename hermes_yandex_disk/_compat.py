"""Compat layer: Hermes' secret resolver when present, an env-only shim in tests.

Guarded with ``ImportError`` ONLY, so the shim kicks in solely when Hermes is
genuinely absent — a real import error (circular import, version mismatch) must
surface rather than silently degrade into a different code path.
"""

from __future__ import annotations

import os

try:
    from agent.web_search_provider import get_provider_env

    HERMES_AVAILABLE = True
except ImportError:  # pragma: no cover - only outside Hermes (tests, standalone use)
    HERMES_AVAILABLE = False

    def get_provider_env(name: str) -> str:  # type: ignore[misc]
        return (os.environ.get(name) or "").strip()


__all__ = ["HERMES_AVAILABLE", "get_provider_env"]
