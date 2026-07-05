"""Bionics MCP Server — exposes the tool registry over MCP (stdio or HTTP).

This server makes Bionics's 187 automation tools available to:
  - Claude Code, Cursor, Windsurf, Claude Desktop (via stdio)
  - Network agents / multiple clients (via HTTP)
  - Any MCP-compatible client

## Usage

### stdio mode (default — local subprocess)
    python mcp_server.py

### HTTP mode (multi-client, network-capable)
    BIONICS_MCP_TRANSPORT=http python mcp_server.py
    # Server listens on http://127.0.0.1:7337 by default

### Allow destructive tools (delete_actor, delete_asset, run_python, etc.)
    BIONICS_MCP_ALLOW_DESTRUCTIVE=true python mcp_server.py

## Environment Variables

| Variable | Default | Purpose |
|----------|---------|---------|
| BIONICS_MCP_TRANSPORT | stdio | "stdio" or "http" |
| BIONICS_MCP_HOST | 127.0.0.1 | HTTP bind host |
| BIONICS_MCP_PORT | 7337 | HTTP bind port |
| BIONICS_MCP_ALLOW_DESTRUCTIVE | false | Allow DESTRUCTIVE-tier tools |
| BIONICS_MCP_PATH | /mcp | HTTP path prefix |

## Client Config

### stdio (Claude Desktop / Claude Code / .mcp.json)
    {
      "mcpServers": {
        "bionics": {
          "command": "python",
          "args": ["<ABSOLUTE_PATH_TO_BIONICS>/mcp_server.py"],
          "cwd": "<ABSOLUTE_PATH_TO_BIONICS>"
        }
      }
    }

### HTTP (multi-client)
    {
      "mcpServers": {
        "bionics": {
          "type": "http",
          "url": "http://127.0.0.1:7337/mcp"
        }
      }
    }

## Architecture
  Client → MCP (stdio|HTTP) → FastMCP → ToolGate → ToolRegistry → tool function
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Any

# Add project root to path so we can import core/ and bionics_tools/
PROJECT_ROOT = Path(__file__).parent.resolve()
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Setup logging BEFORE importing anything that logs
LOG_DIR = PROJECT_ROOT / "audit"
LOG_DIR.mkdir(exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[logging.FileHandler(LOG_DIR / "mcp_server.log", encoding="utf-8")],
)
logger = logging.getLogger("bionics.mcp")

# Import Bionics core
from bionics_tools import register_all
from core.bridge import SafetyTier, ToolGate, get_registry

# Tier policy: safe+moderate auto-approve; destructive require explicit env opt-in
ALLOW_DESTRUCTIVE = os.environ.get("BIONICS_MCP_ALLOW_DESTRUCTIVE", "false").strip().lower() in ("true", "1", "yes", "on")


# ============================================================================
# Register all tools
# ============================================================================

TOOL_COUNT = register_all()
logger.info(f"Registered {TOOL_COUNT} Bionics tools")

# Opt-in observability: BIONICS_OTEL_ENABLE=1 wires OTLP spans for every tool call.
try:
    from core.otel_hook import install_from_env
    if install_from_env():
        logger.info("OpenTelemetry tool-call spans enabled")
except Exception as e:
    logger.debug(f"OTel hook skipped: {e}")


# ============================================================================
# MCP Server Setup
# ============================================================================

try:
    from fastmcp import FastMCP
    HAS_FASTMCP = True
except ImportError:
    try:
        from mcp.server.fastmcp import FastMCP  # type: ignore
        HAS_FASTMCP = True
    except ImportError:
        HAS_FASTMCP = False

if not HAS_FASTMCP:
    print(
        "ERROR: fastmcp not installed. Run:\n"
        "  pip install fastmcp\n"
        "or\n"
        "  pip install 'mcp[cli]'",
        file=sys.stderr,
    )
    sys.exit(1)


INSTRUCTIONS = f"""Bionics — AI Desktop Automation + UE5 Game Dev Toolkit (v0.7 SOTA)

You have access to {TOOL_COUNT} tools across these categories:
  • input       — click, type, hotkey, drag, scroll, mouse control
  • capture     — screenshot, region capture, monitor listing
  • vision      — find_image (template match), OCR
  • system      — windows, processes, clipboard, system info
  • plans       — save/list/execute multi-step automation plans
  • watch       — Bionics Watch Mode state
  • meta        — list_tools, describe_tool, config, version
  • bionics     — divine_powers: NL prompt → MVP Doctor → Author/UE Knowledge context → Plan → (optional) Execute. The unified UE5 entry point — prefer this over orchestrating individual ue5_* tools by hand for AnimBP/combat/AI/movement/save tasks.
  • audit       — audit log tail + filter
  • market      — Market Bot: PDF parse, product CRUD, framework recs, post generation
  • memory      — persistent cross-session memory (remember/recall/search/forget)
                   + Voyager tool-use cache (find proven sequences for warm-start)
  • ue5_actor   — spawn/query/delete/modify actors in UE5
  • ue5_blueprint — query/compile/edit Blueprint graphs + interfaces
  • ue5_asset   — create/save/delete/query UE5 assets + dataasset_bulk_set (batch property writer)
  • ue5_animgraph — AnimGraph node create/wire/query (C++ plugin — full graph control)
                   + Linked Anim Layer AnimBP creation (Bible Step 2)
  • ue5_bpdoctor — Blueprint Doctor scan/fix/results (34 checks incl. MM + Linked Layer)
  • ue5_editor  — editor-side UE5 ops (datatable rows, level save/load/new)
  • ue5_material — inspect/compile/set material params
  • ue5_pie     — start/stop/pause Play-In-Editor
  • ue5_statetree — inspect StateTree assets + add_task (CRUD write, 2026-04-17)
  • ue5_widget  — inspect Widget Blueprints + runtime UMG
  • ue5_profiling — Insights traces, stat commands
  • ue5_python  — execute Python in UE5's interpreter
  • ue5_build   — Live Coding hot reload
  • ue5_runtime — viewport capture, logs, project info, levels
  • ue5_rigging — IK Rig + IK Retargeter + batch retarget (Bible Step 3)
  • ue5_controlrig — Control Rig asset + AnimBP assignment (Bible Step 6)
  • ue5_niagara — VFX spawn + User Exposed Param bind (combat VFX / cymatics)
  • ue5_audio   — SoundWave import + SoundAttenuation configure (audio sprint)
  • ue5_native  — native C++ BionicsBridge plugin bridge (5-20ms vs 100-400ms Python RE)
  • tasks       — async task manager (MCP 2025-11-25 Tasks): submit/status/result/cancel/list

Safety tiers:
  • safe (read-only) — no confirmation
  • moderate (interactive) — 1 confirmation hint
  • destructive (deletions) — 2 confirmations required
  • DESTRUCTIVE blocked by default; set BIONICS_MCP_ALLOW_DESTRUCTIVE=true to enable

Memory scopes (suggested):
  task_outcome | user_preference | app_pattern | failure | learned_fix

Start by calling list_tools() or list_categories() to explore capabilities.
For UE5 tools, first call ue5_connection_status() to verify the editor is running.
For recurring tasks, call bionics_tool_cache_find() before planning to check for
proven warm-start sequences.
"""

mcp = FastMCP("Bionics", instructions=INSTRUCTIONS)

# Shared gate — no Qt safety callback available as MCP server (stdio subprocess).
# Safe + Moderate tools auto-approve; Destructive require BIONICS_MCP_ALLOW_DESTRUCTIVE=true.
_gate = ToolGate()
_gate.set_bypass_safety(True)
logger.info(f"ALLOW_DESTRUCTIVE={ALLOW_DESTRUCTIVE}")


# ============================================================================
# Dynamic tool registration from Bionics ToolRegistry into MCP
# ============================================================================


# FastMCP Context type — optional. When present, FastMCP injects it into async
# wrappers so tools can emit progress notifications, sample, or read session state.
try:
    from fastmcp import Context as _FastMCPContext  # type: ignore
except ImportError:
    _FastMCPContext = None  # type: ignore


def _mcp_tool_error(message: str) -> Exception:
    """Build the exception to raise so FastMCP reports an MCP error (isError) rather
    than emitting structuredContent that would fail a declared outputSchema on a
    failed call. Prefers fastmcp's ToolError; falls back to RuntimeError (FastMCP
    surfaces that as isError too)."""
    try:
        from fastmcp.exceptions import ToolError
        return ToolError(message)
    except Exception:
        return RuntimeError(message)


def _make_mcp_wrapper(tool_name: str):
    """Create an async closure that calls the Bionics tool registry.

    Async so FastMCP's event loop stays responsive under concurrent MCP tool
    calls — each tool body runs inside `asyncio.to_thread` instead of blocking.

    The optional `ctx` parameter lets FastMCP inject the per-call Context for
    tools that want progress notifications, sampling, or session state. The
    ctx is stashed in a ContextVar via `core/mcp_ctx.py` so sync tools running
    under `asyncio.to_thread` (which preserves contextvars) can reach it too.
    """
    if _FastMCPContext is not None:
        async def _wrapper(ctx: _FastMCPContext | None = None, **kwargs: Any) -> dict:
            import asyncio

            from core.mcp_ctx import reset_mcp_context, set_mcp_context
            spec = get_registry().get(tool_name)
            if spec and spec.safety_tier == SafetyTier.DESTRUCTIVE and not ALLOW_DESTRUCTIVE:
                return {
                    "ok": False, "content": "", "data": {},
                    "error": (
                        f"Tool '{tool_name}' is DESTRUCTIVE and blocked by default. "
                        f"Set environment variable BIONICS_MCP_ALLOW_DESTRUCTIVE=true to enable."
                    ),
                    "meta": {"tier": "destructive", "blocked": True},
                }
            token = set_mcp_context(ctx) if ctx is not None else None
            try:
                result = await asyncio.to_thread(_gate.execute, tool_name, kwargs)
                _os = spec.output_schema if spec else None
                if _os is not None and not result.ok:
                    raise _mcp_tool_error(result.error or f"Tool '{tool_name}' failed")
                return result.mcp_structured(_os)
            finally:
                if token is not None:
                    reset_mcp_context(token)
    else:
        # Older FastMCP without Context — async wrapper without ctx injection.
        async def _wrapper(**kwargs: Any) -> dict:  # type: ignore[no-redef]
            import asyncio
            spec = get_registry().get(tool_name)
            if spec and spec.safety_tier == SafetyTier.DESTRUCTIVE and not ALLOW_DESTRUCTIVE:
                return {
                    "ok": False, "content": "", "data": {},
                    "error": (
                        f"Tool '{tool_name}' is DESTRUCTIVE and blocked by default. "
                        f"Set environment variable BIONICS_MCP_ALLOW_DESTRUCTIVE=true to enable."
                    ),
                    "meta": {"tier": "destructive", "blocked": True},
                }
            result = await asyncio.to_thread(_gate.execute, tool_name, kwargs)
            _os = spec.output_schema if spec else None
            if _os is not None and not result.ok:
                raise _mcp_tool_error(result.error or f"Tool '{tool_name}' failed")
            return result.mcp_structured(_os)
    _wrapper.__name__ = tool_name
    return _wrapper


def _register_all_with_mcp() -> int:
    """Register every Bionics tool as an MCP tool.

    FastMCP 3.x doesn't support **kwargs wrappers (can't infer schema).
    We use FunctionTool direct construction with our pre-built JSON schemas.

    Emits MCP-standard annotations (readOnlyHint / destructiveHint /
    idempotentHint / openWorldHint / title) so clients like Claude Code can
    machine-read safety metadata and prompt users appropriately.
    """
    try:
        from fastmcp.tools.function_tool import FunctionTool
    except ImportError:
        logger.error("FunctionTool import failed — fastmcp version may be incompatible")
        return 0

    registry = get_registry()
    registered = 0
    annotations_supported = True  # Track whether FastMCP build accepts annotations kwarg
    output_schema_supported = True  # Track whether FastMCP build accepts output_schema kwarg
    for spec in registry.list_all():
        wrapper = _make_mcp_wrapper(spec.name)
        desc = f"[{spec.category}] {spec.description.split(chr(10))[0]}"
        desc += f"\n\nSafety tier: {spec.safety_tier.value}"
        if spec.annotations.read_only:
            desc += " (read-only)"
        if spec.annotations.destructive:
            desc += " (DESTRUCTIVE)"

        # MCP-standard annotations (spec 2025-11-25 §Tools → Tool.annotations)
        mcp_annotations = {
            "readOnlyHint": spec.annotations.read_only,
            "destructiveHint": spec.annotations.destructive,
            "idempotentHint": spec.annotations.idempotent,
            "openWorldHint": spec.annotations.open_world,
        }
        if spec.annotations.title:
            mcp_annotations["title"] = spec.annotations.title

        # Build kwargs dict so we can conditionally include output_schema without
        # combinatorial branching. FastMCP 3.x accepts `output_schema=`; older
        # builds reject it with TypeError — fall back once and log.
        kwargs = {
            "name": spec.name,
            "description": desc,
            "parameters": spec.input_schema,
            "fn": wrapper,
        }
        if annotations_supported:
            kwargs["annotations"] = mcp_annotations
        if output_schema_supported and spec.output_schema is not None:
            kwargs["output_schema"] = spec.output_schema

        tool = None
        try:
            tool = FunctionTool(**kwargs)
        except TypeError as e:
            msg = str(e)
            retried = False
            if "output_schema" in msg and output_schema_supported:
                output_schema_supported = False
                logger.warning(
                    "FastMCP build rejected output_schema= kwarg; continuing without MCP outputSchema. "
                    "Upgrade fastmcp for typed structuredContent support."
                )
                kwargs.pop("output_schema", None)
                retried = True
            if "annotations" in msg and annotations_supported:
                annotations_supported = False
                logger.warning(
                    "FastMCP build rejected annotations= kwarg; continuing without MCP annotations. "
                    "Upgrade fastmcp to 3.x+ to unlock readOnlyHint/destructiveHint metadata for clients."
                )
                kwargs.pop("annotations", None)
                retried = True
            if retried:
                try:
                    tool = FunctionTool(**kwargs)
                except Exception as e2:
                    logger.warning(f"Failed to register {spec.name} after fallback: {e2}")
                    continue
            else:
                logger.warning(f"Failed to register {spec.name}: {e}")
                continue
        except Exception as e:
            logger.warning(f"Failed to register {spec.name}: {e}")
            continue

        try:
            mcp.add_tool(tool)
            registered += 1
        except Exception as e:
            logger.warning(f"Failed to add {spec.name} to MCP: {e}")

    return registered


REGISTERED = _register_all_with_mcp()
logger.info(f"Exposed {REGISTERED}/{TOOL_COUNT} tools via MCP")


# ============================================================================
# Entry Point
# ============================================================================


def _run_http_server(host: str, port: int, path: str) -> None:
    """Run FastMCP with streamable HTTP transport.

    Tries multiple FastMCP API variants for compatibility with 2.x/3.x.
    """
    # Attempt 1: FastMCP 2.x/3.x — transport="http" or "streamable-http"
    for transport_name in ("http", "streamable-http", "sse"):
        try:
            logger.info(f"Trying transport={transport_name} on http://{host}:{port}{path}")
            # Try with path param (newer API)
            try:
                mcp.run(transport=transport_name, host=host, port=port, path=path)
                return
            except TypeError:
                pass
            # Try without path param (older API)
            mcp.run(transport=transport_name, host=host, port=port)
            return
        except (TypeError, ValueError, AttributeError) as _e:
            logger.debug(f"Transport {transport_name} failed: {_e}")
            continue
        except Exception as _e:
            logger.error(f"HTTP server crashed: {_e}")
            raise
    # All variants failed — fall back to stdio with a warning
    logger.warning("HTTP transport not available in this FastMCP version. Falling back to stdio.")
    mcp.run()


def main():
    transport = os.environ.get("BIONICS_MCP_TRANSPORT", "stdio").strip().lower()
    logger.info(f"Starting Bionics MCP server: transport={transport}")

    if transport in ("http", "streamable-http", "sse"):
        host = os.environ.get("BIONICS_MCP_HOST", "127.0.0.1").strip()
        try:
            port = int(os.environ.get("BIONICS_MCP_PORT", "7337"))
        except ValueError:
            port = 7337
            logger.warning(f"Invalid BIONICS_MCP_PORT, using default {port}")
        path = os.environ.get("BIONICS_MCP_PATH", "/mcp").strip()
        if not path.startswith("/"):
            path = "/" + path
        print(f"Bionics MCP server: http://{host}:{port}{path}", file=sys.stderr)
        print(f"  Tools: {REGISTERED} | Destructive allowed: {ALLOW_DESTRUCTIVE}", file=sys.stderr)
        _run_http_server(host, port, path)
    else:
        # stdio (default)
        print("Bionics MCP server: stdio", file=sys.stderr)
        print(f"  Tools: {REGISTERED} | Destructive allowed: {ALLOW_DESTRUCTIVE}", file=sys.stderr)
        mcp.run()


if __name__ == "__main__":
    main()
