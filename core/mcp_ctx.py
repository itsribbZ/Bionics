"""Thread/async-safe holder for the current MCP Context.

The MCP server wrapper sets this to the FastMCP `Context` object at tool-call
time so sync tools running under `asyncio.to_thread` can still access it (via
automatic `contextvars.copy_context()` propagation when `to_thread` runs).

Usage from inside a tool that wants progress notifications:

    from core.mcp_ctx import get_mcp_context
    ctx = get_mcp_context()
    if ctx is not None:
        # ctx is a fastmcp.Context when running under MCP; None otherwise
        # Note: ctx.report_progress is async — call only from async tools.
        ...

Rationale: keeping this helper in its own module avoids a `core/*` ↔
`mcp_server.py` circular import, since `mcp_server.py` imports every tool
at startup to build the registry.
"""
from __future__ import annotations

import contextvars

_mcp_context_var: contextvars.ContextVar = contextvars.ContextVar(
    "bionics_mcp_context", default=None
)


def set_mcp_context(ctx):
    """Set the current MCP context for this async task. Returns a reset token."""
    return _mcp_context_var.set(ctx)


def reset_mcp_context(token) -> None:
    """Reset the MCP context using a token returned from `set_mcp_context`."""
    _mcp_context_var.reset(token)


def get_mcp_context():
    """Return the current MCP context, or None if not running under MCP."""
    return _mcp_context_var.get()
