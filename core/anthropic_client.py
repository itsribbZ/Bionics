"""Shared Anthropic client singleton.

Prior to this module, Bionics instantiated 7 separate `Anthropic()` clients
across `core/agent.py`, `core/auto_planner.py`, `core/planner.py`,
`core/quiz_engine.py`, `core/verification.py`, `core/watch_engine.py`, and
`bionics_tools/market.py`. Each held its own httpx connection pool, multiplying
TCP keep-alives and file descriptors on long MCP sessions.

This module exposes a singleton-per-API-key keyed registry. Callers that pass
no `api_key` (or the same key as the env var) share the process-wide client.
Callers with a custom key get their own cached instance.

Thread-safety: the Anthropic SDK's underlying httpx client is safe for
concurrent requests per official docs, so one client can serve the agent loop
thread, the GUI thread, and MCP concurrent tool calls simultaneously.

Shutdown: `atexit` closes every cached client to drain keep-alive connections.
"""
from __future__ import annotations

import atexit
import os
import threading

from anthropic import Anthropic

_lock = threading.Lock()
_clients: dict[str, Anthropic] = {}


def get_shared_client(api_key: str | None = None) -> Anthropic:
    """Return a shared Anthropic client keyed by resolved API key.

    If `api_key` is None, resolves from `ANTHROPIC_API_KEY` env var. Raises
    ValueError if no key is resolvable — callers that want to surface a more
    specific error should preflight-check before calling.
    """
    effective = (api_key or os.environ.get("ANTHROPIC_API_KEY", "")).strip()
    if not effective:
        raise ValueError(
            "Anthropic API key not found. Export ANTHROPIC_API_KEY "
            "(get one at https://console.anthropic.com/)."
        )
    with _lock:
        client = _clients.get(effective)
        if client is None:
            client = Anthropic(api_key=effective)
            _clients[effective] = client
        return client


def close_all_clients() -> None:
    """Close every cached client. Idempotent; safe to call multiple times."""
    global _clients
    with _lock:
        for client in _clients.values():
            try:
                client.close()
            except Exception:
                pass
        _clients = {}


atexit.register(close_all_clients)
