"""UE5 Runtime Tools — PIE, viewport, logs, Python execution, console vars.

Matches soft-ue-cli's runtime control surface.
"""

from __future__ import annotations

from typing import Annotated, Literal

from bionics_tools._ue5_common import escape_path, get_bridge, run_python, wrap_script
from core.bridge import SafetyTier, ToolResult, bionics_tool

# ============================================================================
# PIE SESSION CONTROL
# ============================================================================


@bionics_tool(
    name="ue5_pie_start",
    category="ue5_pie",
    safety_tier=SafetyTier.MODERATE,
    aliases=["pie-start"],
    title="Start PIE",
)
def ue5_pie_start(
    mode: Literal["viewport", "new_window", "standalone"] = "viewport",
) -> ToolResult:
    """Start Play-In-Editor session."""
    body = f"""
settings = unreal.LevelEditorPlaySettings()
try:
    pmode_map = {{
        "viewport": unreal.PlayModeType.PLAY_IN_VIEWPORT,
        "new_window": unreal.PlayModeType.PLAY_IN_EDITOR_FLOATING,
        "standalone": unreal.PlayModeType.PLAY_IN_STANDALONE_PROCESS,
    }}
    play_mode = pmode_map.get('{mode}', unreal.PlayModeType.PLAY_IN_VIEWPORT)
    settings.last_executed_play_mode_type = play_mode
    unreal.EditorLevelLibrary.editor_play_simulate()
    print(_dump({{"ok": True, "mode": '{mode}'}}))
except Exception as _pe:
    try:
        unreal.EditorLevelLibrary.editor_play_simulate()
        print(_dump({{"ok": True, "mode": "default"}}))
    except Exception as _e2:
        print(_dump({{"error": str(_e2)}}))
"""
    return run_python(wrap_script(body))


@bionics_tool(
    name="ue5_pie_stop",
    category="ue5_pie",
    safety_tier=SafetyTier.MODERATE,
    aliases=["pie-stop"],
    title="Stop PIE",
)
def ue5_pie_stop() -> ToolResult:
    """Stop Play-In-Editor session."""
    body = """
try:
    unreal.EditorLevelLibrary.editor_end_play()
    print(_dump({"ok": True, "stopped": True}))
except Exception as _se:
    print(_dump({"error": str(_se)}))
"""
    return run_python(wrap_script(body))


@bionics_tool(
    name="ue5_pie_pause",
    category="ue5_pie",
    safety_tier=SafetyTier.MODERATE,
    title="Pause PIE",
)
def ue5_pie_pause() -> ToolResult:
    """Pause the running PIE session."""
    body = """
try:
    world = unreal.EditorLevelLibrary.get_game_world()
    unreal.GameplayStatics.set_game_paused(world, True)
    print(_dump({"ok": True, "paused": True}))
except Exception as _pe:
    print(_dump({"error": str(_pe)}))
"""
    return run_python(wrap_script(body))


@bionics_tool(
    name="ue5_pie_resume",
    category="ue5_pie",
    safety_tier=SafetyTier.MODERATE,
    title="Resume PIE",
)
def ue5_pie_resume() -> ToolResult:
    """Resume the paused PIE session."""
    body = """
try:
    world = unreal.EditorLevelLibrary.get_game_world()
    unreal.GameplayStatics.set_game_paused(world, False)
    print(_dump({"ok": True, "paused": False}))
except Exception as _re:
    print(_dump({"error": str(_re)}))
"""
    return run_python(wrap_script(body))


@bionics_tool(
    name="ue5_pie_state",
    category="ue5_pie",
    safety_tier=SafetyTier.SAFE,
    read_only=True,
    title="PIE State",
)
def ue5_pie_state() -> ToolResult:
    """Get current PIE state: running/paused/stopped."""
    body = """
try:
    world = unreal.EditorLevelLibrary.get_game_world()
    if world is None:
        print(_dump({"state": "stopped", "is_paused": False}))
    else:
        paused = unreal.GameplayStatics.is_game_paused(world)
        print(_dump({"state": "paused" if paused else "running", "is_paused": paused}))
except Exception as _se:
    print(_dump({"state": "unknown", "error": str(_se)}))
"""
    return run_python(wrap_script(body))


# ============================================================================
# VIEWPORT / SCREENSHOTS
# ============================================================================


@bionics_tool(
    name="ue5_capture_viewport",
    category="ue5_runtime",
    safety_tier=SafetyTier.SAFE,
    read_only=True,
    aliases=["capture-viewport"],
    title="Capture Viewport",
)
def ue5_capture_viewport(
    save_path: Annotated[str, "Save path (.png)"] = "viewport.png",
) -> ToolResult:
    """Capture the UE5 viewport to PNG."""
    sp = escape_path(save_path)
    body = f"""
try:
    import os
    unreal.AutomationLibrary.take_high_res_screenshot(1920, 1080, '{sp}')
    print(_dump({{"ok": True, "saved_to": '{sp}'}}))
except Exception as _ce:
    try:
        unreal.SystemLibrary.execute_console_command(None, 'HighResShot 1920x1080 filename={sp}')
        print(_dump({{"ok": True, "saved_to": '{sp}', "method": "console"}}))
    except Exception as _e2:
        print(_dump({{"error": str(_e2)}}))
"""
    return run_python(wrap_script(body))


# ============================================================================
# LOGS
# ============================================================================


@bionics_tool(
    name="ue5_get_logs",
    category="ue5_runtime",
    safety_tier=SafetyTier.SAFE,
    read_only=True,
    aliases=["get-logs"],
    title="Get UE5 Logs",
)
def ue5_get_logs(
    lines: Annotated[int, "Number of recent lines"] = 100,
    filter_text: str = "",
) -> ToolResult:
    """Read recent lines from UE5's log file."""
    ft = escape_path(filter_text)
    body = f"""
try:
    log_path = unreal.Paths.project_log_dir() + unreal.Paths.get_project_file_path().rsplit('/', 1)[-1].replace('.uproject', '.log')
    with open(log_path, 'r', encoding='utf-8', errors='replace') as f:
        all_lines = f.readlines()
    filtered = [l.rstrip() for l in all_lines if not '{ft}' or '{ft}' in l]
    tail = filtered[-{lines}:]
    print(_dump({{"lines": tail, "count": len(tail), "total": len(all_lines)}}))
except Exception as _le:
    print(_dump({{"error": str(_le)}}))
"""
    return run_python(wrap_script(body))


# ============================================================================
# PYTHON EXECUTION
# ============================================================================


@bionics_tool(
    name="ue5_run_python",
    category="ue5_python",
    safety_tier=SafetyTier.DESTRUCTIVE,
    destructive=True,
    open_world=True,
    aliases=["run-python"],
    title="Run Python Script",
)
def ue5_run_python(
    script: Annotated[str, "Python code to execute in UE5 interpreter"],
) -> ToolResult:
    """Execute arbitrary Python code in UE5's embedded interpreter (destructive)."""
    bridge = get_bridge()
    resp = bridge.execute_python(script)
    if not resp.success:
        return ToolResult.failure(f"Execution failed: {resp.error}")
    output = resp.data.get("output", [])
    if isinstance(output, list):
        out_str = "\n".join(
            line if isinstance(line, str) else str(line.get("output", line))
            for line in output
        )
    else:
        out_str = str(output)
    truncated = len(out_str) > 1000
    content = out_str[:1000] + (" ... [truncated]" if truncated else "")
    return ToolResult.success(
        content=content, data={"output": out_str, "truncated": truncated},
    )


@bionics_tool(
    name="ue5_run_python_file",
    category="ue5_python",
    safety_tier=SafetyTier.DESTRUCTIVE,
    destructive=True,
    title="Run Python File",
)
def ue5_run_python_file(
    file_path: Annotated[str, "Path to .py file (must have .py extension)"],
) -> ToolResult:
    """Execute a Python file in UE5's interpreter (only .py files allowed)."""
    from pathlib import Path
    p = Path(file_path)
    if p.suffix.lower() != ".py":
        return ToolResult.failure(
            f"Only .py files are allowed (got: {p.suffix!r})"
        )
    if not p.exists():
        return ToolResult.failure(f"File not found: {file_path}")
    if not p.is_file():
        return ToolResult.failure(f"Not a file: {file_path}")
    # Size limit — prevent reading huge files
    if p.stat().st_size > 1_000_000:  # 1 MB
        return ToolResult.failure(f"File too large (>1MB): {file_path}")
    try:
        script = p.read_text(encoding="utf-8")
    except Exception as e:
        return ToolResult.failure(f"Read failed: {e}")
    return ue5_run_python(script)


# ============================================================================
# PROJECT INFO
# ============================================================================


@bionics_tool(
    name="ue5_project_info",
    category="ue5_runtime",
    safety_tier=SafetyTier.SAFE,
    read_only=True,
    idempotent=True,
    aliases=["project-info"],
    title="Project Info",
    output_schema={
        "type": "object",
        "properties": {
            "project_name": {"type": "string"},
            "project_dir": {"type": "string"},
            "content_dir": {"type": "string"},
            "engine_dir": {"type": "string"},
            "engine_version": {"type": "string"},
            "error": {"type": "string"},
        },
    },
)
def ue5_project_info() -> ToolResult:
    """Get UE5 project name, engine version, paths."""
    body = """
try:
    info = {
        "project_name": unreal.Paths.project_name() if hasattr(unreal.Paths, 'project_name') else '',
        "project_dir": unreal.Paths.project_dir(),
        "content_dir": unreal.Paths.project_content_dir(),
        "engine_dir": unreal.Paths.engine_dir(),
        "engine_version": unreal.SystemLibrary.get_engine_version(),
    }
    print(_dump(info))
except Exception as _ie:
    print(_dump({"error": str(_ie)}))
"""
    return run_python(wrap_script(body))


@bionics_tool(
    name="ue5_class_hierarchy",
    category="ue5_runtime",
    safety_tier=SafetyTier.SAFE,
    read_only=True,
    title="Class Hierarchy",
)
def ue5_class_hierarchy(
    class_name: str,
    direction: Literal["parents", "children", "both"] = "both",
    depth: int = 5,
) -> ToolResult:
    """Get class ancestors/descendants (native + Blueprint)."""
    cn = escape_path(class_name)
    body = f"""
try:
    cls = getattr(unreal, '{cn}', None)
    if cls is None:
        cls = unreal.load_class(None, '{cn}')
    if cls is None:
        print(_dump({{"error": "class not found: {cn}"}}))
    else:
        parents = []
        current = cls
        for _ in range({depth}):
            try:
                sup = current.get_super_class()
                if sup is None or sup == current:
                    break
                parents.append(sup.get_name())
                current = sup
            except Exception:
                break
        print(_dump({{"class": '{cn}', "parents": parents}}))
except Exception as _he:
    print(_dump({{"error": str(_he)}}))
"""
    return run_python(wrap_script(body))
