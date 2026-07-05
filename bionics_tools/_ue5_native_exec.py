"""Shared native-:8090 deferred-exec transport for Bionics tools.

Extracted on the rule of three — the third consumer of the fire-and-poll handshake
(ue5_uasvc skeletal import + ue5_autorig validate/rig + the planner's native-first
ue5_python execution path) — so the transport lives in exactly one place.

The C++ BionicsBridge runs Python on UE5's GAME THREAD, so ``execute_console_command``
with ``py exec(...)`` returns ``deferred=true`` immediately — there is NO synchronous
return value. The real result is written by the UE5-side script to a JSON file that the
caller polls (delete-then-poll, so any appearance is a fresh result; no mtime races).

Two entry points:
  * ``fire_and_poll`` — the tool transport: caller stages its OWN result-writing script
    (uasvc/autorig do this), fires it, polls the result JSON. Returns the parsed dict.
  * ``run_python_native`` — the planner transport: wraps an ARBITRARY script so its
    stdout/stderr + exception are captured into a result JSON, fires, polls. Gives back a
    normalized ``{reachable, success, output, error}`` so a caller can fall back to another
    transport when the native bridge is unreachable.

``invoke`` (the bridge caller, normally ``call_bridge_tool``) is passed in by each caller so
that each caller's module-level binding of ``call_bridge_tool`` stays the unit-test patch
seam (tests do ``patch.object(ue5_uasvc, "call_bridge_tool", ...)``).
"""

from __future__ import annotations

import json
import tempfile
import time
import uuid
from collections.abc import Callable
from pathlib import Path

from bionics_tools.ue5_native import _configured_ue5_project_dir
from core.bridge import ToolResult

# UE5-side wrapper for an ARBITRARY planner script. Runs the user script in a fresh
# __main__ namespace with stdout+stderr captured and any exception trapped, then writes a
# {success, output, error, traceback} JSON the host polls. The two scratch paths are the
# only parametrized pieces (sentinels). Runs inside UE5's interpreter via py exec.
_PLAN_STEP_WRAPPER = r'''
import json
import io
import contextlib
import traceback

RESULT_PATH = r"__RESULT_PATH__"
USER_SCRIPT = r"__USER_SCRIPT__"

_res = {"success": False, "output": "", "error": "", "traceback": ""}
_buf = io.StringIO()
try:
    with open(USER_SCRIPT, "r", encoding="utf-8") as _f:
        _code = _f.read()
    with contextlib.redirect_stdout(_buf), contextlib.redirect_stderr(_buf):
        exec(compile(_code, USER_SCRIPT, "exec"), {"__name__": "__main__"})
    _res["success"] = True
except Exception as _e:  # noqa: BLE001 — capture any failure for the host
    _res["error"] = "{}: {}".format(type(_e).__name__, _e)
    _res["traceback"] = traceback.format_exc()
_res["output"] = _buf.getvalue()

with open(RESULT_PATH, "w", encoding="utf-8") as _f:
    json.dump(_res, _f)

try:
    import unreal
    unreal.log("BIONICS_PLAN_STEP_RESULT: success=" + str(_res["success"]))
except Exception:  # noqa: BLE001 — logging is best-effort
    pass
'''

_PLAN_STEP_MARKER = "BIONICS_PLAN_STEP_RESULT"


def resolve_scratch_dir(subdir: str) -> Path | None:
    """Scratch dir for the handshake files (params + script + result).

    Lives under the UE5 project's ``Saved/Bionics/<subdir>`` so BOTH this process and the
    editor can read/write it; falls back to the system temp dir (``bionics_<subdir>``, same
    user) when the project path isn't configured. Returns None only if the dir can't be made.
    """
    proj = _configured_ue5_project_dir()
    if proj:
        d = Path(proj) / "Saved" / "Bionics" / subdir
    else:
        d = Path(tempfile.gettempdir()) / f"bionics_{subdir}"
    try:
        d.mkdir(parents=True, exist_ok=True)
        return d
    except OSError:
        return None


def _poll_for_result(
    result_path: Path, timeout_s: float, poll_interval_s: float = 0.5
) -> tuple[dict | None, str]:
    """Poll ``result_path`` until it parses as JSON or ``timeout_s`` elapses.

    Returns ``(parsed_dict, "")`` on success or ``(None, last_read_error)`` on timeout. The
    caller must have deleted ``result_path`` before staging so any appearance is a fresh write.
    """
    deadline = time.monotonic() + max(1.0, float(timeout_s))
    last_err = ""
    while time.monotonic() < deadline:
        if result_path.exists():
            try:
                return json.loads(result_path.read_text(encoding="utf-8")), ""
            except (ValueError, OSError) as e:
                last_err = str(e)  # result file mid-write — keep polling
        time.sleep(poll_interval_s)
    return None, last_err


def fire_and_poll(
    script_path: Path,
    result_path: Path,
    timeout_s: float,
    *,
    invoke: Callable[[str, dict], object],
    noun: str = "command",
    marker: str = "BIONICS_RESULT",
    poll_interval_s: float = 0.5,
) -> tuple[ToolResult | None, dict | None]:
    """Fire a UE5-side script over :8090 (deferred game-thread exec) and poll for its result
    JSON. Returns ``(error_result, None)`` on failure, or ``(None, parsed_dict)`` on success.

    The caller stages a script that writes ``result_path`` itself, and must delete
    ``result_path`` BEFORE staging. ``invoke`` is the bridge caller (``call_bridge_tool``),
    passed in so the caller's module-level binding remains the unit-test patch seam. ``noun``
    and ``marker`` reproduce each caller's original messages verbatim.
    """
    cmd = f"py exec(open(r'{script_path.as_posix()}').read())"
    fire = invoke("execute_console_command", {"command": cmd})
    if not fire.ok:
        return ToolResult.failure(
            f"Bridge could not queue the {noun} command: {fire.error}. "
            "Is UE5 + BionicsBridge running? (native :8090)"
        ), None

    data, last_err = _poll_for_result(result_path, timeout_s, poll_interval_s)
    if data is not None:
        return None, data

    msg = (
        f"Timed out after {float(timeout_s):.0f}s waiting for the deferred {noun} result "
        f"({result_path.name}). The command was queued to UE5's game thread — check the "
        f"editor log for the {marker} marker."
    )
    if last_err:
        msg += f" Last read error: {last_err}"
    return ToolResult.failure(msg), None


def run_python_native(
    script_text: str,
    timeout_s: float,
    *,
    invoke: Callable[[str, dict], object],
    subdir: str = "planner",
    poll_interval_s: float = 0.5,
) -> dict:
    """Run an ARBITRARY Python script inside UE5 over the native :8090 bridge.

    The deferred game-thread bridge has no synchronous return, so the script is wrapped to
    capture its stdout/stderr + any exception into a result JSON that this polls. Returns a
    normalized dict::

        {"reachable": bool, "success": bool, "output": str, "error": str, "traceback": str}

    ``reachable=False`` means the native path was not usable — the bridge refused/could not
    queue the command, OR a local setup failure (no writable scratch dir, staging write
    failed) prevented even attempting it. A local-FS failure is NOT a bridge signal, so it
    also returns reachable=False — the caller may fall back to another transport (which does
    not need our scratch files). ``reachable=True`` with ``success=False`` is a real native
    failure: the script was queued and then raised, returned nothing, or timed out.

    Per-call uuid-suffixed scratch filenames keep concurrent callers from racing on shared
    files; the handshake files are cleaned up best-effort once the poll returns.
    """
    scratch = resolve_scratch_dir(subdir)
    if scratch is None:
        # A local-FS failure is NOT a bridge-reachability signal — let the caller fall back
        # to the RC transport (which sends the script inline and needs no scratch files).
        return {"reachable": False, "success": False, "output": "",
                "error": "could not create a writable scratch dir for native exec (setup)",
                "traceback": ""}

    # Per-call uuid suffix so concurrent callers (e.g. parallel divine_powers runs sharing
    # the 'planner' subdir) never race on the same handshake filenames.
    uid = uuid.uuid4().hex[:8]
    user_path = scratch / f"step_user_{uid}.py"
    wrap_path = scratch / f"step_wrapped_{uid}.py"
    result_path = scratch / f"step_result_{uid}.json"

    try:
        try:
            user_path.write_text(script_text, encoding="utf-8")
            wrapped = (
                _PLAN_STEP_WRAPPER
                .replace("__RESULT_PATH__", result_path.as_posix())
                .replace("__USER_SCRIPT__", user_path.as_posix())
            )
            wrap_path.write_text(wrapped, encoding="utf-8")
        except OSError as e:
            # Staging is local-FS work, not a bridge attempt — fall back to RC.
            return {"reachable": False, "success": False, "output": "",
                    "error": f"failed to stage native-exec handshake files in {scratch}: {e}",
                    "traceback": ""}

        cmd = f"py exec(open(r'{wrap_path.as_posix()}').read())"
        fire = invoke("execute_console_command", {"command": cmd})
        if not fire.ok:
            return {"reachable": False, "success": False, "output": "",
                    "error": getattr(fire, "error", "") or "native :8090 bridge unreachable",
                    "traceback": ""}

        data, last_err = _poll_for_result(result_path, timeout_s, poll_interval_s)
        if data is None:
            msg = (
                f"Native exec timed out after {float(timeout_s):.0f}s — the {result_path.name} "
                f"result never appeared. The script was queued to UE5's game thread; check the "
                f"editor log for the {_PLAN_STEP_MARKER} marker."
            )
            if last_err:
                msg += f" Last read error: {last_err}"
            return {"reachable": True, "success": False, "output": "", "error": msg, "traceback": ""}

        return {
            "reachable": True,
            "success": bool(data.get("success")),
            "output": data.get("output", ""),
            "error": data.get("error", ""),
            "traceback": data.get("traceback", ""),
        }
    finally:
        # Best-effort cleanup. The game thread reads both scripts within its first tick (well
        # before any multi-second poll), so removing them after the poll returns is race-free
        # and keeps the scratch dir from accumulating one file-set per call.
        for _p in (user_path, wrap_path, result_path):
            try:
                _p.unlink(missing_ok=True)
            except OSError:
                pass
