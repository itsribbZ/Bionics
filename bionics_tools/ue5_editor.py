"""UE5 Editor Tools — StateTree, Widget, Material, Profiling, Build, Live Coding.

The broadest surface — matches soft-ue-cli's advanced editor tools.
"""

from __future__ import annotations

from typing import Annotated, Literal

from bionics_tools._ue5_common import escape_path, run_python, safe_json_literal, wrap_script
from core.bridge import SafetyTier, ToolResult, bionics_tool

# ============================================================================
# MATERIAL
# ============================================================================


@bionics_tool(
    name="ue5_query_material",
    category="ue5_material",
    safety_tier=SafetyTier.SAFE,
    read_only=True,
    aliases=["query-material"],
    title="Query Material",
)
def ue5_query_material(
    asset_path: str,
    include: Literal["parameters", "graph", "all"] = "parameters",
) -> ToolResult:
    """Inspect a Material or Material Instance — parameters, expressions."""
    ap = escape_path(asset_path)
    body = f"""
mat = unreal.load_asset('{ap}')
if not mat:
    print(_dump({{"error": "not found"}}))
else:
    info = {{"path": '{ap}', "class": mat.get_class().get_name()}}
    try:
        if '{include}' in ('parameters', 'all'):
            scalars = unreal.MaterialEditingLibrary.get_scalar_parameter_names(mat) if hasattr(unreal, 'MaterialEditingLibrary') else []
            vectors = unreal.MaterialEditingLibrary.get_vector_parameter_names(mat) if hasattr(unreal, 'MaterialEditingLibrary') else []
            textures = unreal.MaterialEditingLibrary.get_texture_parameter_names(mat) if hasattr(unreal, 'MaterialEditingLibrary') else []
            info["parameters"] = {{
                "scalars": [str(s) for s in scalars],
                "vectors": [str(v) for v in vectors],
                "textures": [str(t) for t in textures],
            }}
    except Exception as _me:
        info["error"] = str(_me)
    print(_dump(info))
"""
    return run_python(wrap_script(body))


@bionics_tool(
    name="ue5_compile_material",
    category="ue5_material",
    safety_tier=SafetyTier.MODERATE,
    aliases=["compile-material"],
    title="Compile Material",
)
def ue5_compile_material(asset_path: str) -> ToolResult:
    """Recompile a Material asset."""
    ap = escape_path(asset_path)
    body = f"""
mat = unreal.load_asset('{ap}')
if not mat:
    print(_dump({{"error": "not found"}}))
else:
    try:
        unreal.MaterialEditingLibrary.recompile_material(mat)
        print(_dump({{"ok": True}}))
    except Exception as _ce:
        print(_dump({{"error": str(_ce)}}))
"""
    return run_python(wrap_script(body))


@bionics_tool(
    name="ue5_set_material_scalar",
    category="ue5_material",
    safety_tier=SafetyTier.MODERATE,
    strict=True,
    title="Set Material Scalar",
)
def ue5_set_material_scalar(
    asset_path: str,
    parameter_name: str,
    value: float,
) -> ToolResult:
    """Set a scalar parameter on a Material Instance."""
    ap = escape_path(asset_path)
    pn = escape_path(parameter_name)
    body = f"""
mat = unreal.load_asset('{ap}')
if not mat:
    print(_dump({{"error": "not found"}}))
else:
    try:
        unreal.MaterialEditingLibrary.set_material_instance_scalar_parameter_value(mat, '{pn}', {value})
        print(_dump({{"ok": True, "parameter": '{pn}', "value": {value}}}))
    except Exception as _se:
        print(_dump({{"error": str(_se)}}))
"""
    return run_python(wrap_script(body))


@bionics_tool(
    name="ue5_set_material_vector",
    category="ue5_material",
    safety_tier=SafetyTier.MODERATE,
    strict=True,
    title="Set Material Vector",
)
def ue5_set_material_vector(
    asset_path: str,
    parameter_name: str,
    rgba: Annotated[list[float], "[R,G,B,A] 0-1 (3 or 4 elements)"],
) -> ToolResult:
    """Set a vector/color parameter on a Material Instance."""
    if not isinstance(rgba, list) or len(rgba) < 3:
        return ToolResult.failure(
            "rgba must be a list of 3 or 4 floats: [R, G, B] or [R, G, B, A]"
        )
    try:
        r, g, b = float(rgba[0]), float(rgba[1]), float(rgba[2])
        a = float(rgba[3]) if len(rgba) > 3 else 1.0
    except (TypeError, ValueError) as _e:
        return ToolResult.failure(f"rgba values must be numeric: {_e}")
    ap = escape_path(asset_path)
    pn = escape_path(parameter_name)
    body = f"""
mat = unreal.load_asset('{ap}')
if not mat:
    print(_dump({{"error": "not found"}}))
else:
    try:
        col = unreal.LinearColor({r}, {g}, {b}, {a})
        unreal.MaterialEditingLibrary.set_material_instance_vector_parameter_value(mat, '{pn}', col)
        print(_dump({{"ok": True, "parameter": '{pn}'}}))
    except Exception as _ve:
        print(_dump({{"error": str(_ve)}}))
"""
    return run_python(wrap_script(body))


# ============================================================================
# STATETREE
# ============================================================================


@bionics_tool(
    name="ue5_query_statetree",
    category="ue5_statetree",
    safety_tier=SafetyTier.SAFE,
    read_only=True,
    aliases=["query-statetree"],
    title="Query StateTree",
)
def ue5_query_statetree(asset_path: str) -> ToolResult:
    """Inspect a StateTree asset — states, transitions, tasks."""
    ap = escape_path(asset_path)
    body = f"""
st = unreal.load_asset('{ap}')
if not st:
    print(_dump({{"error": "not found"}}))
else:
    info = {{"path": '{ap}', "class": st.get_class().get_name()}}
    try:
        editor_data = st.get_editor_property('edit_data') or st.get_editor_property('editor_data')
        if editor_data:
            states = editor_data.get_editor_property('sub_trees') or []
            info["state_count"] = len(states)
            info["states"] = [s.get_editor_property('name') if hasattr(s, 'get_editor_property') else str(s) for s in states]
    except Exception as _qe:
        info["error"] = str(_qe)
    print(_dump(info))
"""
    return run_python(wrap_script(body))


@bionics_tool(
    name="ue5_statetree_add_task",
    category="ue5_statetree",
    safety_tier=SafetyTier.MODERATE,
    strict=True,
    aliases=["statetree-add-task"],
    title="Add StateTree Task",
)
def ue5_statetree_add_task(
    asset_path: Annotated[str, "StateTree asset path"],
    state_name: Annotated[str, "Target state name (from query_statetree)"],
    task_class: Annotated[str, "Task node class (e.g. StateTreeRunEnvQueryTask, BTTask_MoveTo)"],
    properties: Annotated[dict, "Dict of property_name → value for the task"] = None,
) -> ToolResult:
    """Add a task node to a named state in a StateTree.

    Write complement to ue5_query_statetree. Appends a new task instance of
    task_class to the target state's tasks array, optionally with property
    overrides. Use for AI archetype differentiation — e.g. adding a
    StateTreeRunEnvQueryTask to ST_RangedSniper for distance-based targeting.

    Caveats: UE5 StateTree Python API is limited. This uses property
    reflection; it works for simple task types with instanced data, may fail
    on tasks with deeply-nested struct properties. Returns detailed error so
    you know when to fall back to the editor UI.
    """
    ap = escape_path(asset_path)
    sn = escape_path(state_name)
    tc = escape_path(task_class)
    props = properties if isinstance(properties, dict) else {}
    props_b64 = safe_json_literal(props)
    body = f"""
import base64 as _b64
asset_path = '{ap}'
state_name = '{sn}'
task_class_name = '{tc}'
props = json.loads(_b64.b64decode('{props_b64}').decode('utf-8'))

st = unreal.load_asset(asset_path)
if not st:
    print(_dump({{"ok": False, "error": f"StateTree not found: {{asset_path}}"}}))
else:
    editor_data = st.get_editor_property('edit_data') or st.get_editor_property('editor_data')
    if not editor_data:
        print(_dump({{"ok": False, "error": "StateTree editor_data not accessible"}}))
    else:
        states = editor_data.get_editor_property('sub_trees') or []
        target_state = None
        for s in states:
            try:
                if s.get_editor_property('name') == state_name or str(s.get_editor_property('name')) == state_name:
                    target_state = s
                    break
            except Exception:
                continue
        if not target_state:
            available = [str(s.get_editor_property('name')) for s in states if hasattr(s, 'get_editor_property')]
            print(_dump({{"ok": False, "error": f"state '{{state_name}}' not found", "available_states": available}}))
        else:
            # Resolve task class via class path lookup
            task_cls = unreal.load_class(None, task_class_name)
            if not task_cls:
                # Try with /Script/ prefix paths
                for prefix in ('/Script/StateTreeModule.', '/Script/AIModule.', '/Script/GameplayStateTreeModule.'):
                    task_cls = unreal.load_class(None, prefix + task_class_name)
                    if task_cls: break
            if not task_cls:
                print(_dump({{"ok": False, "error": f"task class not found: {{task_class_name}}"}}))
            else:
                try:
                    new_task = unreal.new_object(task_cls)
                    for pname, pval in props.items():
                        try:
                            new_task.set_editor_property(pname, pval)
                        except Exception as _spe:
                            pass  # skip properties that don't exist on this task class
                    tasks = list(target_state.get_editor_property('tasks') or [])
                    tasks.append(new_task)
                    target_state.set_editor_property('tasks', tasks)
                    unreal.EditorAssetLibrary.save_asset(asset_path)
                    print(_dump({{
                        "ok": True,
                        "path": asset_path,
                        "state": state_name,
                        "task_class": task_class_name,
                        "tasks_after": len(tasks),
                    }}))
                except Exception as _ae:
                    print(_dump({{"ok": False, "error": str(_ae), "hint": "StateTree Python API may not support this task type — try editor UI"}}))
"""
    return run_python(wrap_script(body))


# ============================================================================
# WIDGET BLUEPRINT
# ============================================================================


@bionics_tool(
    name="ue5_query_widget",
    category="ue5_widget",
    safety_tier=SafetyTier.SAFE,
    read_only=True,
    aliases=["inspect-widget"],
    title="Inspect Widget Blueprint",
)
def ue5_query_widget(asset_path: str) -> ToolResult:
    """Inspect a Widget Blueprint hierarchy."""
    ap = escape_path(asset_path)
    body = f"""
wb = unreal.load_asset('{ap}')
if not wb:
    print(_dump({{"error": "not found"}}))
else:
    info = {{"path": '{ap}', "class": wb.get_class().get_name()}}
    try:
        widget_tree = wb.get_editor_property('widget_tree')
        if widget_tree:
            root = widget_tree.get_editor_property('root_widget')
            info["root_widget"] = root.get_name() if root else None
            info["root_class"] = root.get_class().get_name() if root else None
    except Exception as _we:
        info["error"] = str(_we)
    print(_dump(info))
"""
    return run_python(wrap_script(body))


@bionics_tool(
    name="ue5_list_widgets_runtime",
    category="ue5_widget",
    safety_tier=SafetyTier.SAFE,
    read_only=True,
    title="List Runtime Widgets",
)
def ue5_list_widgets_runtime() -> ToolResult:
    """Inspect widgets currently on screen during PIE."""
    body = """
try:
    world = unreal.EditorLevelLibrary.get_game_world()
    if not world:
        print(_dump({"error": "no game world (PIE not running)"}))
    else:
        widgets = unreal.UMGWidgetBlueprintLibrary.get_all_widgets_of_class(world, unreal.UserWidget)
        result = [{"name": w.get_name(), "class": w.get_class().get_name()} for w in widgets]
        print(_dump({"widgets": result, "count": len(result)}))
except Exception as _le:
    print(_dump({"error": str(_le)}))
"""
    return run_python(wrap_script(body))


# ============================================================================
# PROFILING / INSIGHTS
# ============================================================================


@bionics_tool(
    name="ue5_start_trace",
    category="ue5_profiling",
    safety_tier=SafetyTier.MODERATE,
    aliases=["insights-start"],
    title="Start Insights Trace",
)
def ue5_start_trace(
    channels: Annotated[list[str] | None, "Trace channels"] = None,
) -> ToolResult:
    """Start a UE Insights .utrace capture."""
    chan_list = channels if channels else ["cpu", "gpu", "memory"]
    # Validate channel names are safe (alphanumeric only)
    for ch in chan_list:
        if not isinstance(ch, str) or not ch.replace("_", "").isalnum():
            return ToolResult.failure(f"Invalid channel name: {ch!r}")
    channels_str = ",".join(chan_list)
    body = f"""
try:
    unreal.SystemLibrary.execute_console_command(None, 'Trace.Start {channels_str}')
    print(_dump({{"ok": True, "channels": "{channels_str}"}}))
except Exception as _te:
    print(_dump({{"error": str(_te)}}))
"""
    return run_python(wrap_script(body))


@bionics_tool(
    name="ue5_stop_trace",
    category="ue5_profiling",
    safety_tier=SafetyTier.MODERATE,
    aliases=["insights-stop"],
    title="Stop Insights Trace",
)
def ue5_stop_trace() -> ToolResult:
    """Stop the active Insights trace and save it."""
    body = """
try:
    unreal.SystemLibrary.execute_console_command(None, 'Trace.Stop')
    print(_dump({"ok": True, "stopped": True}))
except Exception as _se:
    print(_dump({"error": str(_se)}))
"""
    return run_python(wrap_script(body))


@bionics_tool(
    name="ue5_stat_command",
    category="ue5_profiling",
    safety_tier=SafetyTier.MODERATE,
    title="Stat Command",
)
def ue5_stat_command(
    stat: Annotated[str, "Stat name (fps, unit, scenerendering, gpu, etc.)"] = "fps",
) -> ToolResult:
    """Execute a stat command (stat fps, stat unit, etc.)."""
    s = escape_path(stat)
    body = f"""
try:
    unreal.SystemLibrary.execute_console_command(None, 'stat {s}')
    print(_dump({{"ok": True, "stat": '{s}'}}))
except Exception as _se:
    print(_dump({{"error": str(_se)}}))
"""
    return run_python(wrap_script(body))


# ============================================================================
# BUILD / LIVE CODING
# ============================================================================


@bionics_tool(
    name="ue5_live_coding",
    category="ue5_build",
    safety_tier=SafetyTier.MODERATE,
    aliases=["live-coding"],
    title="Trigger Live Coding",
)
def ue5_live_coding() -> ToolResult:
    """Trigger Live Coding hot reload (Ctrl+Alt+F11).

    T1.A native-first re-route (godspeed 2026-05-15): tries C++ :8090 bridge
    first (returns structured module_loaded + triggered fields, ~5-20ms vs
    100-400ms Python-RE). Falls back to Python remote-exec when bridge is
    unreachable. See `feedback_routing_matrix_correction.md`.
    """
    from bionics_tools.ue5_native import call_bridge_tool
    native = call_bridge_tool("live_coding_compile", {})
    if native.ok or "Bridge unreachable" not in (native.error or ""):
        return native
    body = """
try:
    unreal.SystemLibrary.execute_console_command(None, 'LiveCoding.Compile')
    print(_dump({"ok": True, "triggered": True}))
except Exception as _le:
    print(_dump({"error": str(_le)}))
"""
    return run_python(wrap_script(body))


# ============================================================================
# DATATABLE
# ============================================================================


@bionics_tool(
    name="ue5_datatable_rows",
    category="ue5_asset",
    safety_tier=SafetyTier.SAFE,
    read_only=True,
    title="DataTable Rows",
)
def ue5_datatable_rows(asset_path: str) -> ToolResult:
    """List all row names in a DataTable."""
    ap = escape_path(asset_path)
    body = f"""
dt = unreal.load_asset('{ap}')
if not dt:
    print(_dump({{"error": "not found"}}))
else:
    try:
        rows = unreal.DataTableFunctionLibrary.get_data_table_row_names(dt)
        print(_dump({{"rows": [str(r) for r in rows], "count": len(rows)}}))
    except Exception as _de:
        print(_dump({{"error": str(_de)}}))
"""
    return run_python(wrap_script(body))


# NOTE: ue5_datatable_add_row removed 2026-04-16 — was a SAFE-tier stub that
# always returned ok=False because UE5 Python API does not expose DataTable.add_row().
# LLM callers would select it and always fail. Implement via CSV import helper
# or C++ bridge tool if DataTable row insertion is needed in the future.


# ============================================================================
# LEVEL MANAGEMENT
# ============================================================================


@bionics_tool(
    name="ue5_save_level",
    category="ue5_runtime",
    safety_tier=SafetyTier.MODERATE,
    title="Save Current Level",
)
def ue5_save_level() -> ToolResult:
    """Save the currently open level."""
    body = """
try:
    ok = unreal.EditorLevelLibrary.save_current_level()
    print(_dump({"ok": ok}))
except Exception as _se:
    print(_dump({"error": str(_se)}))
"""
    return run_python(wrap_script(body))


@bionics_tool(
    name="ue5_load_level",
    category="ue5_runtime",
    safety_tier=SafetyTier.MODERATE,
    title="Load Level",
)
def ue5_load_level(level_path: Annotated[str, "/Game/Maps/LevelName"]) -> ToolResult:
    """Load a level asset into the editor."""
    lp = escape_path(level_path)
    body = f"""
try:
    ok = unreal.EditorLevelLibrary.load_level('{lp}')
    print(_dump({{"ok": ok, "level": '{lp}'}}))
except Exception as _le:
    print(_dump({{"error": str(_le)}}))
"""
    return run_python(wrap_script(body))


@bionics_tool(
    name="ue5_new_level",
    category="ue5_runtime",
    safety_tier=SafetyTier.MODERATE,
    title="New Level",
)
def ue5_new_level(level_path: str) -> ToolResult:
    """Create a new empty level."""
    lp = escape_path(level_path)
    body = f"""
try:
    ok = unreal.EditorLevelLibrary.new_level('{lp}')
    print(_dump({{"ok": ok, "created": '{lp}'}}))
except Exception as _ne:
    print(_dump({{"error": str(_ne)}}))
"""
    return run_python(wrap_script(body))
