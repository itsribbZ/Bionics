"""Shared UE5 tool helpers — bridge singleton, Python script runner, JSON parsing."""

from __future__ import annotations

import json
import logging
from typing import Any

from core.bridge import ToolResult

logger = logging.getLogger("bionics.tools.ue5")

_bridge_instance = None


def get_bridge():
    """Return the shared UE5Bridge instance (lazy init, reads config.yaml)."""
    global _bridge_instance
    if _bridge_instance is None:
        from pathlib import Path

        from core.ue5_bridge import UE5Bridge
        try:
            import yaml
            config_path = Path(__file__).parent.parent / "config.yaml"
            if config_path.exists():
                with open(config_path) as f:
                    cfg = yaml.safe_load(f) or {}
                ue5_cfg = cfg.get("ue5", {})
                rc = ue5_cfg.get("remote_control", {})
                py = ue5_cfg.get("python_execution", {})
                _bridge_instance = UE5Bridge(
                    rc_host=rc.get("host", "127.0.0.1"),
                    rc_port=rc.get("port", 30010),
                    python_port=py.get("port", 9998),
                )
            else:
                _bridge_instance = UE5Bridge()
        except Exception as e:
            logger.warning(f"_ue5_common: config load failed ({e}) — falling back to UE5Bridge defaults")
            _bridge_instance = UE5Bridge()
    return _bridge_instance


def ensure_connected() -> tuple[bool, str]:
    """Check UE5 connection. Returns (connected, status_message)."""
    bridge = get_bridge()
    status = bridge.check_connection()
    if bridge.is_connected:
        return (True, "connected")
    return (False, f"UE5 not available: {status.name}")


def run_python(script: str, expect_json: bool = True) -> ToolResult:
    """Run a Python script in UE5's interpreter and parse JSON output.

    The script should print a JSON object/array as its last output line
    using `unreal.Json.to_json_string(result)` or `print(json.dumps(result))`.
    """
    connected, status = ensure_connected()
    if not connected:
        return ToolResult.failure(f"UE5 bridge unavailable: {status}")

    bridge = get_bridge()
    resp = bridge.execute_python(script)
    if not resp.success:
        return ToolResult.failure(
            f"Python execution failed: {resp.error}",
            ue5_error=resp.error,
        )

    output_lines = resp.data.get("output", [])
    if isinstance(output_lines, list):
        raw_output = "\n".join(
            line if isinstance(line, str) else str(line.get("output", line))
            for line in output_lines
        )
    else:
        raw_output = str(output_lines)

    if not expect_json:
        return ToolResult.success(
            content=raw_output[:500], data={"output": raw_output},
        )

    # Try to parse JSON from output
    parsed = _extract_json(raw_output)
    if parsed is None:
        return ToolResult.success(
            content=raw_output[:500],
            data={"output": raw_output, "note": "no JSON detected"},
        )

    summary = _summarize(parsed)
    return ToolResult.success(
        content=summary, data=parsed if isinstance(parsed, dict) else {"result": parsed},
    )


def _extract_json(text: str) -> Any:
    """Try to extract a JSON object/array from stdout."""
    text = text.strip()
    if not text:
        return None
    # Try the whole thing first
    try:
        return json.loads(text)
    except (ValueError, json.JSONDecodeError):
        pass
    # Try last line
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    for line in reversed(lines):
        if line.startswith(("{", "[")):
            try:
                return json.loads(line)
            except (ValueError, json.JSONDecodeError):
                continue
    return None


def _summarize(obj: Any) -> str:
    """Short summary string for a parsed JSON result."""
    if isinstance(obj, dict):
        if "error" in obj:
            return f"UE5 error: {obj['error']}"
        if "count" in obj:
            return f"count={obj['count']}"
        return f"object keys: {list(obj.keys())[:5]}"
    if isinstance(obj, list):
        return f"list[{len(obj)}]"
    return str(obj)[:100]


# ============================================================================
# Python script helpers — inlined into tool modules via f-strings
# ============================================================================


PY_HEADER = """
import json
import unreal
try:
    from unreal import Json as _Json
    def _dump(obj):
        try:
            return _Json.to_json_string(obj)
        except Exception:
            return json.dumps(obj, default=str)
except Exception:
    def _dump(obj):
        return json.dumps(obj, default=str)
"""


def wrap_script(body: str) -> str:
    """Wrap a Python body with header + error handling, guaranteeing JSON out."""
    return f"""
{PY_HEADER}
try:
{_indent(body, 4)}
except Exception as _e:
    import traceback as _tb
    print(_dump({{"error": str(_e), "trace": _tb.format_exc()}}))
"""


def _indent(text: str, spaces: int) -> str:
    pad = " " * spaces
    return "\n".join(pad + line for line in text.splitlines())


def escape_path(path: str) -> str:
    """Escape a string for safe inclusion in a single-quoted Python string literal.

    Handles: backslashes, single quotes, newlines, carriage returns, null bytes,
    and triple-quote sequences. This is the foundational safety layer for all
    UE5 Python script generation.
    """
    if not isinstance(path, str):
        path = str(path)
    return (
        path
        .replace("\\", "/")         # UE5 paths use forward slashes
        .replace("'", "\\'")        # escape single quotes
        .replace('"', '\\"')        # escape double quotes
        .replace("\n", "\\n")       # escape newlines
        .replace("\r", "\\r")       # escape carriage returns
        .replace("\x00", "")        # strip null bytes
        .replace("'''", "\\'\\'\\'")  # break up triple-quote sequences
    )


def safe_json_literal(obj) -> str:
    """Serialize a Python object to a base64-encoded JSON string for safe injection.

    Returns a tuple of (python_decoding_snippet, var_name_with_data).
    Use to embed user-supplied structured data into generated scripts without
    risk of triple-quote or quote-escape injection.
    """
    import base64
    import json as _json
    data_json = _json.dumps(obj, default=str)
    b64 = base64.b64encode(data_json.encode("utf-8")).decode("ascii")
    return b64
