"""UE5 Native Bridge Tools — talks to the C++ BionicsBridge plugin directly.

Unlike the Python Remote Execution bridge (ue5_actor, ue5_blueprint, etc.),
these tools hit the C++ plugin via HTTP JSON-RPC on localhost:8090. Benefits:

    • 5-20ms round-trip (vs 100-400ms for Python RE)
    • Works in packaged builds (not just editor)
    • Structured JSON responses with proper error handling
    • No UE5 Python plugin dependency

Requires the BionicsBridge plugin to be installed and running in the UE5
project. See plugins/BionicsBridge/README.md for setup.

Discovery: the plugin writes <ProjectDir>/.bionics-bridge/instance.json —
tools walk up from cwd to find it, OR you can set BIONICS_BRIDGE_URL.
"""

from __future__ import annotations

import itertools
import json
import logging
import os
from pathlib import Path
from typing import Annotated

from core.bridge import SafetyTier, ToolResult, bionics_tool

logger = logging.getLogger("bionics.tools.ue5_native")

DEFAULT_BRIDGE_URL = "http://127.0.0.1:8090/bridge"
_request_id_counter = itertools.count(1)


def _next_id() -> int:
    return next(_request_id_counter)


def _discover_bridge() -> tuple[str, str]:
    """Find the BionicsBridge (url, token) via env vars or discovery file.

    Resolution order:
      1. BIONICS_BRIDGE_URL / BIONICS_BRIDGE_TOKEN env vars
      2. <project>/.bionics-bridge/instance.json (walked up from cwd)
      3. DEFAULT_BRIDGE_URL + empty token (auth-disabled dev fallback)
    """
    env_url = os.environ.get("BIONICS_BRIDGE_URL", "").strip()
    env_token = os.environ.get("BIONICS_BRIDGE_TOKEN", "").strip()
    if env_url and env_token:
        return env_url, env_token

    url = env_url or ""
    token = env_token or ""
    cwd = Path.cwd()
    for parent in [cwd, *cwd.parents]:
        candidate = parent / ".bionics-bridge" / "instance.json"
        if candidate.exists():
            try:
                data = json.loads(candidate.read_text(encoding="utf-8"))
                if not url:
                    url = data.get("url") or ""
                if not token:
                    token = data.get("token") or ""
                if url and token:
                    return url, token
                if url:  # token may legitimately be empty if plugin is pre-auth build
                    return url, token
            except Exception as _e:
                pass  # config read failed, fall through to default
    return (url or DEFAULT_BRIDGE_URL), token


def _discover_bridge_url() -> str:
    """Backward-compat shim — prefer _discover_bridge() for new code."""
    return _discover_bridge()[0]


def _call_bridge(method: str, params: dict | None = None, timeout: float = 30.0) -> dict:
    """Call a JSON-RPC method on the BionicsBridge plugin.

    Returns parsed JSON-RPC response dict (may contain 'result' or 'error').
    Automatically attaches Authorization: Bearer <token> when a token is discoverable.
    """
    import requests
    url, token = _discover_bridge()
    envelope = {
        "jsonrpc": "2.0",
        "id": _next_id(),
        "method": method,
        "params": params or {},
    }
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    resp = requests.post(url, json=envelope, timeout=timeout, headers=headers)
    if resp.status_code == 401:
        raise RuntimeError(
            "BionicsBridge auth failed (401). Token is in <ProjectDir>/.bionics-bridge/instance.json; "
            "make sure the plugin was started after the most recent rebuild, or set BIONICS_BRIDGE_TOKEN."
        )
    resp.raise_for_status()
    return resp.json()


def call_bridge_tool(tool_name: str, arguments: dict | None = None) -> ToolResult:
    """Call a tool on the BionicsBridge C++ plugin via JSON-RPC.

    Public API — use this from other bionics_tools modules (e.g. ue5_animgraph).
    """
    return _call_tool(tool_name, arguments)


def _call_tool(tool_name: str, arguments: dict | None = None) -> ToolResult:
    """Helper: call tools/call on the bridge and normalize the response."""
    try:
        response = _call_bridge("tools/call", {
            "name": tool_name,
            "arguments": arguments or {},
        })
    except Exception as e:
        return ToolResult.failure(
            f"Bridge unreachable: {e}. "
            f"Is the BionicsBridge plugin installed + running? "
            f"URL: {_discover_bridge_url()}"
        )
    if "error" in response:
        err = response["error"]
        return ToolResult.failure(
            f"Bridge error ({err.get('code', '?')}): {err.get('message', 'unknown')}"
        )
    result = response.get("result", {})
    content = result.get("content", [])
    is_error = result.get("isError", False)
    text = ""
    parsed_data = {}
    if content:
        text = content[0].get("text", "")
        try:
            parsed_data = json.loads(text)
        except (ValueError, json.JSONDecodeError):
            pass
    return ToolResult(
        ok=not is_error,
        content=text[:500],
        data=parsed_data if parsed_data else {"text": text},
        error=text if is_error else "",
    )


# ============================================================================
# Status / Health
# ============================================================================


@bionics_tool(
    name="ue5_native_status",
    category="ue5_native",
    safety_tier=SafetyTier.SAFE,
    read_only=True,
    title="UE5 Native Bridge Status",
)
def ue5_native_status() -> ToolResult:
    """Check if the BionicsBridge C++ plugin is running in UE5."""
    import requests
    url = _discover_bridge_url()
    # Use the GET health endpoint
    health_url = url
    try:
        resp = requests.get(health_url, timeout=5.0)
        resp.raise_for_status()
        data = resp.json()
        return ToolResult.success(
            content=f"Bridge OK: {data.get('name')} v{data.get('version')}, {data.get('tools')} tools",
            data={**data, "url": url},
        )
    except Exception as e:
        return ToolResult.failure(
            f"Bridge unavailable at {url}: {e}. "
            f"Install the BionicsBridge plugin in your UE5 project."
        )


@bionics_tool(
    name="ue5_native_list_tools",
    category="ue5_native",
    safety_tier=SafetyTier.SAFE,
    read_only=True,
    title="List Bridge Tools",
)
def ue5_native_list_tools() -> ToolResult:
    """List all tools exposed by the BionicsBridge C++ plugin."""
    try:
        response = _call_bridge("tools/list", {})
    except Exception as e:
        return ToolResult.failure(f"Bridge unreachable: {e}")
    if "error" in response:
        return ToolResult.failure(f"Bridge error: {response['error']}")
    result = response.get("result", {})
    tools = result.get("tools", [])
    return ToolResult.success(
        content=f"{len(tools)} tools available via BionicsBridge",
        data={"tools": tools, "count": len(tools)},
    )


# ============================================================================
# Native tools (wrap the C++ plugin's built-in tools)
# ============================================================================


@bionics_tool(
    name="ue5_native_get_actors",
    category="ue5_native",
    safety_tier=SafetyTier.SAFE,
    read_only=True,
    title="Get Actors (Native)",
)
def ue5_native_get_actors(
    class_filter: str = "",
    name_filter: str = "",
    limit: Annotated[int, "Max results (1-10000)"] = 100,
) -> ToolResult:
    """List actors in the current UE5 world via native C++ bridge."""
    return _call_tool("get_actors", {
        "class_filter": class_filter,
        "name_filter": name_filter,
        "limit": max(1, min(int(limit), 10000)),
    })


@bionics_tool(
    name="ue5_native_spawn_actor",
    category="ue5_native",
    safety_tier=SafetyTier.MODERATE,
    title="Spawn Actor (Native)",
)
def ue5_native_spawn_actor(
    actor_class: Annotated[str, "BP path or native class name"],
    location: Annotated[list[float] | None, "[X,Y,Z]"] = None,
    rotation: Annotated[list[float] | None, "[Pitch,Yaw,Roll]"] = None,
    label: str = "",
    editor_world: Annotated[bool, "Spawn in editor world (vs PIE/game)"] = True,
) -> ToolResult:
    """Spawn an actor in UE5 via the native C++ bridge (~5-20ms)."""
    loc = location if location else [0.0, 0.0, 0.0]
    rot = rotation if rotation else [0.0, 0.0, 0.0]
    if len(loc) < 3 or len(rot) < 3:
        return ToolResult.failure("location/rotation must have 3 elements each")
    try:
        loc = [float(loc[0]), float(loc[1]), float(loc[2])]
        rot = [float(rot[0]), float(rot[1]), float(rot[2])]
    except (TypeError, ValueError) as e:
        return ToolResult.failure(f"Non-numeric location/rotation: {e}")
    tool_name = "spawn_actor_editor" if editor_world else "spawn_actor_runtime"
    return _call_tool(tool_name, {
        "actor_class": actor_class,
        "location": loc,
        "rotation": rot,
        "label": label,
    })


@bionics_tool(
    name="ue5_native_console_command",
    category="ue5_native",
    safety_tier=SafetyTier.MODERATE,
    title="Console Command (Native)",
)
def ue5_native_console_command(command: str) -> ToolResult:
    """Execute a UE console command via the native C++ bridge."""
    return _call_tool("execute_console_command", {"command": command})


@bionics_tool(
    name="ue5_native_get_cvar",
    category="ue5_native",
    safety_tier=SafetyTier.SAFE,
    read_only=True,
    title="Get CVar (Native)",
)
def ue5_native_get_cvar(name: str) -> ToolResult:
    """Read a console variable via the native C++ bridge."""
    return _call_tool("get_console_var", {"name": name})


@bionics_tool(
    name="ue5_native_set_cvar",
    category="ue5_native",
    safety_tier=SafetyTier.MODERATE,
    title="Set CVar (Native)",
)
def ue5_native_set_cvar(name: str, value: str) -> ToolResult:
    """Set a console variable via the native C++ bridge."""
    return _call_tool("set_console_var", {"name": name, "value": value})


@bionics_tool(
    name="ue5_native_project_info",
    category="ue5_native",
    safety_tier=SafetyTier.SAFE,
    read_only=True,
    idempotent=True,
    title="Project Info (Native)",
)
def ue5_native_project_info() -> ToolResult:
    """Return UE5 project info via the native C++ bridge."""
    return _call_tool("get_project_info", {})


@bionics_tool(
    name="ue5_native_compile_blueprint",
    category="ue5_native",
    safety_tier=SafetyTier.MODERATE,
    title="Compile Blueprint (Native)",
)
def ue5_native_compile_blueprint(
    asset_path: Annotated[str, "Blueprint asset path (/Game/...)"],
) -> ToolResult:
    """Compile a Blueprint via the native C++ bridge (editor only)."""
    return _call_tool("compile_blueprint", {"asset_path": asset_path})


@bionics_tool(
    name="ue5_native_save_asset",
    category="ue5_native",
    safety_tier=SafetyTier.MODERATE,
    title="Save Asset (Native)",
)
def ue5_native_save_asset(asset_path: str) -> ToolResult:
    """Save a UE5 asset via the native C++ bridge (editor only)."""
    return _call_tool("save_asset", {"asset_path": asset_path})


@bionics_tool(
    name="ue5_native_query_assets",
    category="ue5_native",
    safety_tier=SafetyTier.SAFE,
    read_only=True,
    title="Query Assets (Native)",
)
def ue5_native_query_assets(
    class_name: str = "",
    path_prefix: str = "/Game",
    limit: int = 100,
) -> ToolResult:
    """Search the Content Browser via the native C++ bridge."""
    return _call_tool("query_assets", {
        "class_name": class_name,
        "path_prefix": path_prefix,
        "limit": max(1, min(int(limit), 1000)),
    })
