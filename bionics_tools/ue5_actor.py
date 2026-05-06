"""UE5 Actor Tools — Spawn, Query, Modify, Delete, Batch, Component, Input.

Matches soft-ue-cli's actor/level operation surface via UE5's Python API.
All tools run via remote_execution.py (UE5 Python Remote Execution protocol).
"""

from __future__ import annotations

from typing import Annotated

from bionics_tools._ue5_common import escape_path, run_python, safe_json_literal, wrap_script
from core.bridge import SafetyTier, ToolResult, bionics_tool

# ============================================================================
# SPAWN / DELETE
# ============================================================================


@bionics_tool(
    name="ue5_spawn_actor",
    category="ue5_actor",
    safety_tier=SafetyTier.MODERATE,
    destructive=False,
    aliases=["spawn-actor", "ue5_spawn"],
    title="Spawn Actor",
)
def ue5_spawn_actor(
    actor_class: Annotated[str, "BP path (/Game/...) or native class name (StaticMeshActor)"],
    location: Annotated[list[float] | None, "[X,Y,Z] world location"] = None,
    rotation: Annotated[list[float] | None, "[Pitch,Yaw,Roll]"] = None,
    label: str = "",
) -> ToolResult:
    """Spawn an actor (Blueprint or native class) at a world location."""
    # Sanitize all user inputs before embedding in generated Python
    class_path = escape_path(actor_class)
    lbl = escape_path(label) if label else "Spawned"
    # Defensive-copy mutable defaults
    loc_list = list(location) if location else [0.0, 0.0, 0.0]
    rot_list = list(rotation) if rotation else [0.0, 0.0, 0.0]
    if len(loc_list) < 3 or len(rot_list) < 3:
        return ToolResult.failure("location and rotation must have 3 elements each")
    # Coerce to floats (prevents non-numeric injection via list)
    try:
        lx, ly, lz = float(loc_list[0]), float(loc_list[1]), float(loc_list[2])
        rp, ry, rr = float(rot_list[0]), float(rot_list[1]), float(rot_list[2])
    except (TypeError, ValueError) as _e:
        return ToolResult.failure(f"location/rotation must be numeric: {_e}")
    body = f"""
world = unreal.EditorLevelLibrary.get_editor_world()
actor_cls = unreal.load_class(None, '{class_path}')
if actor_cls is None:
    asset = unreal.load_asset('{class_path}')
    if asset is not None:
        actor_cls = asset.generated_class() if hasattr(asset, 'generated_class') else None
if actor_cls is None:
    try:
        actor_cls = getattr(unreal, '{class_path}')
    except AttributeError:
        actor_cls = None
if actor_cls is None:
    print(_dump({{"error": "class not found: {class_path}"}}))
else:
    loc = unreal.Vector({lx}, {ly}, {lz})
    rot = unreal.Rotator({rp}, {ry}, {rr})
    actor = unreal.EditorLevelLibrary.spawn_actor_from_class(actor_cls, loc, rot)
    if actor is None:
        print(_dump({{"error": "spawn failed"}}))
    else:
        actor.set_actor_label('{lbl}')
        print(_dump({{
            "ok": True,
            "name": actor.get_name(),
            "label": actor.get_actor_label(),
            "class": actor.get_class().get_name(),
            "location": [{lx}, {ly}, {lz}],
        }}))
"""
    return run_python(wrap_script(body))


@bionics_tool(
    name="ue5_batch_spawn",
    category="ue5_actor",
    safety_tier=SafetyTier.MODERATE,
    strict=True,
    title="Batch Spawn Actors",
)
def ue5_batch_spawn(
    actors: Annotated[list[dict], "List of {class, location, rotation, label} dicts"],
) -> ToolResult:
    """Spawn multiple actors in a single transaction."""
    if not isinstance(actors, list) or not actors:
        return ToolResult.failure("actors must be a non-empty list")
    actors_b64 = safe_json_literal(actors)
    body = f"""
import base64 as _b64
spec = json.loads(_b64.b64decode('{actors_b64}').decode('utf-8'))
world = unreal.EditorLevelLibrary.get_editor_world()
results = []
for item in spec:
    cls_path = item.get('class', '')
    loc = item.get('location', [0,0,0])
    rot = item.get('rotation', [0,0,0])
    label = item.get('label', '')
    actor_cls = unreal.load_class(None, cls_path)
    if actor_cls is None:
        try:
            asset = unreal.load_asset(cls_path)
            actor_cls = asset.generated_class() if asset else None
        except Exception as _e:
            actor_cls = None  # class not loadable from path
    if actor_cls is None:
        results.append({{"class": cls_path, "ok": False, "error": "class not found"}})
        continue
    v = unreal.Vector(loc[0], loc[1], loc[2])
    r = unreal.Rotator(rot[0], rot[1], rot[2])
    actor = unreal.EditorLevelLibrary.spawn_actor_from_class(actor_cls, v, r)
    if actor and label:
        actor.set_actor_label(label)
    results.append({{
        "class": cls_path,
        "ok": actor is not None,
        "name": actor.get_name() if actor else None,
    }})
print(_dump({{"spawned": sum(1 for r in results if r['ok']), "total": len(results), "results": results}}))
"""
    return run_python(wrap_script(body))


@bionics_tool(
    name="ue5_delete_actor",
    category="ue5_actor",
    safety_tier=SafetyTier.DESTRUCTIVE,
    destructive=True,
    title="Delete Actor",
)
def ue5_delete_actor(actor_name: Annotated[str, "Actor name or label"]) -> ToolResult:
    """Delete a named actor from the current level."""
    name = escape_path(actor_name)
    body = f"""
world = unreal.EditorLevelLibrary.get_editor_world()
actors = unreal.EditorLevelLibrary.get_all_level_actors()
target = None
for a in actors:
    if a.get_name() == '{name}' or a.get_actor_label() == '{name}':
        target = a
        break
if target is None:
    print(_dump({{"error": "actor not found: {name}"}}))
else:
    unreal.EditorLevelLibrary.destroy_actor(target)
    print(_dump({{"ok": True, "deleted": '{name}'}}))
"""
    return run_python(wrap_script(body))


@bionics_tool(
    name="ue5_batch_delete",
    category="ue5_actor",
    safety_tier=SafetyTier.DESTRUCTIVE,
    destructive=True,
    strict=True,
    title="Batch Delete Actors",
)
def ue5_batch_delete(
    actor_names: Annotated[list[str], "List of actor names or labels to delete"],
) -> ToolResult:
    """Delete multiple actors by name in a single transaction."""
    if not isinstance(actor_names, list) or not actor_names:
        return ToolResult.failure("actor_names must be a non-empty list")
    names_b64 = safe_json_literal(actor_names)
    body = f"""
import base64 as _b64
names = json.loads(_b64.b64decode('{names_b64}').decode('utf-8'))
actors = unreal.EditorLevelLibrary.get_all_level_actors()
deleted = []
for name in names:
    for a in actors:
        if a.get_name() == name or a.get_actor_label() == name:
            unreal.EditorLevelLibrary.destroy_actor(a)
            deleted.append(name)
            break
print(_dump({{"deleted": deleted, "count": len(deleted), "requested": len(names)}}))
"""
    return run_python(wrap_script(body))


# ============================================================================
# QUERY
# ============================================================================


@bionics_tool(
    name="ue5_query_level",
    category="ue5_actor",
    safety_tier=SafetyTier.SAFE,
    read_only=True,
    idempotent=False,
    aliases=["query-level"],
    title="Query Level Actors",
)
def ue5_query_level(
    class_filter: Annotated[str, "Filter by class name (StaticMeshActor, etc.)"] = "",
    name_filter: Annotated[str, "Substring match on actor name/label"] = "",
    tag_filter: Annotated[str, "Actor must have this tag"] = "",
    include_components: bool = False,
    include_transforms: bool = True,
    limit: int = 100,
) -> ToolResult:
    """List actors in the current level with filters and transforms."""
    cf = escape_path(class_filter or "")
    nf = escape_path(name_filter or "")
    tf = escape_path(tag_filter or "")
    # Clamp limit to safe range
    limit = max(1, min(int(limit), 1000))
    body = f"""
actors = unreal.EditorLevelLibrary.get_all_level_actors()
results = []
for a in actors:
    if '{cf}' and a.get_class().get_name() != '{cf}' and '{cf}' not in a.get_class().get_name():
        continue
    if '{nf}':
        nl = a.get_name().lower() + a.get_actor_label().lower()
        if '{nf}'.lower() not in nl:
            continue
    if '{tf}':
        tags = [str(t) for t in a.tags]
        if '{tf}' not in tags:
            continue
    info = {{
        "name": a.get_name(),
        "label": a.get_actor_label(),
        "class": a.get_class().get_name(),
    }}
    if {str(include_transforms).lower()}:
        loc = a.get_actor_location()
        rot = a.get_actor_rotation()
        info["location"] = [loc.x, loc.y, loc.z]
        info["rotation"] = [rot.pitch, rot.yaw, rot.roll]
    if {str(include_components).lower()}:
        info["components"] = [c.get_name() for c in a.get_components_by_class(unreal.ActorComponent)]
    results.append(info)
    if len(results) >= {limit}:
        break
print(_dump({{"actors": results, "count": len(results)}}))
"""
    return run_python(wrap_script(body))


@bionics_tool(
    name="ue5_get_selected",
    category="ue5_actor",
    safety_tier=SafetyTier.SAFE,
    read_only=True,
    title="Get Selected Actors",
    output_schema={
        "type": "object",
        "properties": {
            "selected": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "label": {"type": "string"},
                        "class": {"type": "string"},
                    },
                    "required": ["name", "label", "class"],
                },
            },
            "count": {"type": "integer"},
        },
        "required": ["selected", "count"],
    },
)
def ue5_get_selected() -> ToolResult:
    """Return the list of currently selected actors in the level editor."""
    body = """
selected = unreal.EditorLevelLibrary.get_selected_level_actors()
results = [
    {
        "name": a.get_name(),
        "label": a.get_actor_label(),
        "class": a.get_class().get_name(),
    }
    for a in selected
]
print(_dump({"selected": results, "count": len(results)}))
"""
    return run_python(wrap_script(body))


@bionics_tool(
    name="ue5_select_actors",
    category="ue5_actor",
    safety_tier=SafetyTier.MODERATE,
    title="Select Actors",
)
def ue5_select_actors(
    actor_names: Annotated[list[str], "Actor names or labels to select"],
) -> ToolResult:
    """Set the level editor selection to the named actors."""
    if not isinstance(actor_names, list):
        return ToolResult.failure("actor_names must be a list")
    names_b64 = safe_json_literal(actor_names)
    body = f"""
import base64 as _b64
names = json.loads(_b64.b64decode('{names_b64}').decode('utf-8'))
actors = unreal.EditorLevelLibrary.get_all_level_actors()
to_select = [a for a in actors if a.get_name() in names or a.get_actor_label() in names]
unreal.EditorLevelLibrary.set_selected_level_actors(to_select)
print(_dump({{"selected": len(to_select), "requested": len(names)}}))
"""
    return run_python(wrap_script(body))


# ============================================================================
# PROPERTIES / FUNCTIONS
# ============================================================================


@bionics_tool(
    name="ue5_get_property",
    category="ue5_actor",
    safety_tier=SafetyTier.SAFE,
    read_only=True,
    title="Get Actor Property",
)
def ue5_get_property(
    actor_name: str,
    property_name: Annotated[str, "Property name or dot-path (e.g. RootComponent.Mobility)"],
) -> ToolResult:
    """Read a UPROPERTY on an actor (supports component.property dot notation)."""
    an = escape_path(actor_name)
    pn = escape_path(property_name)
    body = f"""
actors = unreal.EditorLevelLibrary.get_all_level_actors()
target = None
for a in actors:
    if a.get_name() == '{an}' or a.get_actor_label() == '{an}':
        target = a
        break
if not target:
    print(_dump({{"error": "actor not found"}}))
else:
    obj = target
    for part in '{pn}'.split('.')[:-1]:
        obj = obj.get_editor_property(part)
        if obj is None:
            break
    last = '{pn}'.split('.')[-1]
    try:
        val = obj.get_editor_property(last) if obj else None
        print(_dump({{"actor": '{an}', "property": '{pn}', "value": str(val)}}))
    except Exception as _pe:
        print(_dump({{"error": str(_pe)}}))
"""
    return run_python(wrap_script(body))


@bionics_tool(
    name="ue5_set_property",
    category="ue5_actor",
    safety_tier=SafetyTier.MODERATE,
    title="Set Actor Property",
)
def ue5_set_property(
    actor_name: str,
    property_name: Annotated[str, "Property name or dot-path"],
    value: Annotated[str, "Property value as string (will be parsed)"],
) -> ToolResult:
    """Set a UPROPERTY on an actor."""
    an = escape_path(actor_name)
    pn = escape_path(property_name)
    value_b64 = safe_json_literal(value)
    body = f"""
import base64 as _b64
actors = unreal.EditorLevelLibrary.get_all_level_actors()
target = None
for a in actors:
    if a.get_name() == '{an}' or a.get_actor_label() == '{an}':
        target = a
        break
if not target:
    print(_dump({{"error": "actor not found"}}))
else:
    raw = json.loads(_b64.b64decode('{value_b64}').decode('utf-8'))
    try:
        import ast
        parsed = ast.literal_eval(raw) if isinstance(raw, str) else raw
    except Exception as _e:
        parsed = raw  # value not parseable, pass through raw
    obj = target
    parts = '{pn}'.split('.')
    for p in parts[:-1]:
        obj = obj.get_editor_property(p)
    last = parts[-1]
    try:
        obj.set_editor_property(last, parsed)
        print(_dump({{"ok": True, "actor": '{an}', "property": '{pn}', "set_to": raw}}))
    except Exception as _pe:
        print(_dump({{"error": str(_pe)}}))
"""
    return run_python(wrap_script(body))


@bionics_tool(
    name="ue5_call_function",
    category="ue5_actor",
    safety_tier=SafetyTier.MODERATE,
    title="Call Actor Function",
)
def ue5_call_function(
    actor_name: str,
    function_name: str,
    arguments: Annotated[dict | None, "Dict of function arguments"] = None,
) -> ToolResult:
    """Call a BlueprintCallable UFUNCTION on an actor."""
    an = escape_path(actor_name)
    fn = escape_path(function_name)
    args = arguments if arguments else {}
    args_b64 = safe_json_literal(args)
    body = f"""
import base64 as _b64
actors = unreal.EditorLevelLibrary.get_all_level_actors()
target = None
for a in actors:
    if a.get_name() == '{an}' or a.get_actor_label() == '{an}':
        target = a
        break
if not target:
    print(_dump({{"error": "actor not found"}}))
else:
    args = json.loads(_b64.b64decode('{args_b64}').decode('utf-8'))
    try:
        # Try direct call
        method = getattr(target, '{fn}', None)
        if method is not None and callable(method):
            result = method(**args) if args else method()
            print(_dump({{"ok": True, "result": str(result)}}))
        else:
            # Try call_method for BP functions
            result = target.call_method('{fn}', args=tuple(args.values()) if args else ())
            print(_dump({{"ok": True, "result": str(result)}}))
    except Exception as _ce:
        print(_dump({{"error": str(_ce)}}))
"""
    return run_python(wrap_script(body))


@bionics_tool(
    name="ue5_add_component",
    category="ue5_actor",
    safety_tier=SafetyTier.MODERATE,
    title="Add Component",
)
def ue5_add_component(
    actor_name: str,
    component_class: Annotated[str, "Component class name (StaticMeshComponent, etc.)"],
    component_name: str = "",
) -> ToolResult:
    """Add a component to a runtime actor."""
    an = escape_path(actor_name)
    cc = escape_path(component_class)
    cn = escape_path(component_name)
    body = f"""
actors = unreal.EditorLevelLibrary.get_all_level_actors()
target = None
for a in actors:
    if a.get_name() == '{an}' or a.get_actor_label() == '{an}':
        target = a
        break
if not target:
    print(_dump({{"error": "actor not found"}}))
else:
    try:
        cls = getattr(unreal, '{cc}', None)
        if cls is None:
            print(_dump({{"error": "component class not found: {cc}"}}))
        else:
            comp = target.add_component_by_class(cls, False, unreal.Transform(), False)
            if '{cn}':
                comp.rename('{cn}')
            print(_dump({{"ok": True, "component": comp.get_name() if comp else None}}))
    except Exception as _ae:
        print(_dump({{"error": str(_ae)}}))
"""
    return run_python(wrap_script(body))


# ============================================================================
# TRANSFORM / MOVE
# ============================================================================


@bionics_tool(
    name="ue5_set_transform",
    category="ue5_actor",
    safety_tier=SafetyTier.MODERATE,
    title="Set Actor Transform",
)
def ue5_set_transform(
    actor_name: str,
    location: list[float] | None = None,
    rotation: list[float] | None = None,
    scale: list[float] | None = None,
) -> ToolResult:
    """Move/rotate/scale an actor (only updates specified components)."""
    an = escape_path(actor_name)
    # Coerce each vector to numeric floats to prevent injection
    def _validate_vec3(v, name):
        if v is None:
            return None
        if not isinstance(v, (list, tuple)) or len(v) < 3:
            raise ValueError(f"{name} must be a 3-element list [x,y,z]")
        return [float(v[0]), float(v[1]), float(v[2])]
    try:
        loc = _validate_vec3(location, "location")
        rot = _validate_vec3(rotation, "rotation")
        scale = _validate_vec3(scale, "scale")
    except (ValueError, TypeError) as _e:
        return ToolResult.failure(f"Invalid transform: {_e}")

    loc_py = f"[{loc[0]}, {loc[1]}, {loc[2]}]" if loc else "None"
    rot_py = f"[{rot[0]}, {rot[1]}, {rot[2]}]" if rot else "None"
    scale_py = f"[{scale[0]}, {scale[1]}, {scale[2]}]" if scale else "None"
    body = f"""
actors = unreal.EditorLevelLibrary.get_all_level_actors()
target = None
for a in actors:
    if a.get_name() == '{an}' or a.get_actor_label() == '{an}':
        target = a
        break
if not target:
    print(_dump({{"error": "actor not found"}}))
else:
    loc = {loc_py}
    rot = {rot_py}
    scale = {scale_py}
    if loc is not None:
        target.set_actor_location(unreal.Vector(loc[0], loc[1], loc[2]), False, False)
    if rot is not None:
        target.set_actor_rotation(unreal.Rotator(rot[0], rot[1], rot[2]), False)
    if scale is not None:
        target.set_actor_scale3d(unreal.Vector(scale[0], scale[1], scale[2]))
    t = target.get_actor_transform()
    print(_dump({{
        "ok": True,
        "location": [t.translation.x, t.translation.y, t.translation.z],
        "rotation": [t.rotation.to_euler().x, t.rotation.to_euler().y, t.rotation.to_euler().z],
        "scale": [t.scale3d.x, t.scale3d.y, t.scale3d.z],
    }}))
"""
    return run_python(wrap_script(body))


@bionics_tool(
    name="ue5_batch_modify",
    category="ue5_actor",
    safety_tier=SafetyTier.MODERATE,
    strict=True,
    title="Batch Modify Transforms",
)
def ue5_batch_modify(
    modifications: Annotated[list[dict], "List of {actor, location?, rotation?, scale?}"],
) -> ToolResult:
    """Apply transforms to multiple actors in one transaction."""
    if not isinstance(modifications, list):
        return ToolResult.failure("modifications must be a list")
    mods_b64 = safe_json_literal(modifications)
    body = f"""
import base64 as _b64
mods = json.loads(_b64.b64decode('{mods_b64}').decode('utf-8'))
actors = unreal.EditorLevelLibrary.get_all_level_actors()
by_name = {{a.get_name(): a for a in actors}}
by_label = {{a.get_actor_label(): a for a in actors}}
applied = 0
for m in mods:
    name = m.get('actor', '')
    actor = by_name.get(name) or by_label.get(name)
    if not actor:
        continue
    if 'location' in m:
        l = m['location']
        actor.set_actor_location(unreal.Vector(l[0], l[1], l[2]), False, False)
    if 'rotation' in m:
        r = m['rotation']
        actor.set_actor_rotation(unreal.Rotator(r[0], r[1], r[2]), False)
    if 'scale' in m:
        s = m['scale']
        actor.set_actor_scale3d(unreal.Vector(s[0], s[1], s[2]))
    applied += 1
print(_dump({{"applied": applied, "total": len(mods)}}))
"""
    return run_python(wrap_script(body))


# ============================================================================
# CONSOLE
# ============================================================================


@bionics_tool(
    name="ue5_console_command",
    category="ue5_actor",
    safety_tier=SafetyTier.MODERATE,
    title="UE5 Console Command",
)
def ue5_console_command(command: str) -> ToolResult:
    """Execute a UE5 console command."""
    cmd = escape_path(command)
    body = f"""
unreal.SystemLibrary.execute_console_command(None, '{cmd}')
print(_dump({{"ok": True, "command": '{cmd}'}}))
"""
    return run_python(wrap_script(body))


@bionics_tool(
    name="ue5_get_cvar",
    category="ue5_actor",
    safety_tier=SafetyTier.SAFE,
    read_only=True,
    title="Get Console Variable",
)
def ue5_get_cvar(name: str) -> ToolResult:
    """Read a UE5 console variable value."""
    n = escape_path(name)
    body = f"""
cvar = unreal.SystemLibrary.get_console_variable_float_value('{n}')
s_cvar = unreal.SystemLibrary.get_console_variable_string_value('{n}')
print(_dump({{"name": '{n}', "float": cvar, "string": s_cvar}}))
"""
    return run_python(wrap_script(body))


@bionics_tool(
    name="ue5_set_cvar",
    category="ue5_actor",
    safety_tier=SafetyTier.MODERATE,
    title="Set Console Variable",
)
def ue5_set_cvar(name: str, value: str) -> ToolResult:
    """Set a UE5 console variable."""
    n = escape_path(name)
    v = escape_path(value)
    body = f"""
unreal.SystemLibrary.execute_console_command(None, '{n} {v}')
print(_dump({{"ok": True, "cvar": '{n}', "value": '{v}'}}))
"""
    return run_python(wrap_script(body))


# ============================================================================
# CONNECTION CHECK
# ============================================================================


@bionics_tool(
    name="ue5_connection_status",
    category="ue5_actor",
    safety_tier=SafetyTier.SAFE,
    read_only=True,
    idempotent=False,
    title="UE5 Connection Status",
)
def ue5_connection_status() -> ToolResult:
    """Check if UE5 Editor is running and Remote Control is reachable."""
    from bionics_tools._ue5_common import ensure_connected, get_bridge
    connected, msg = ensure_connected()
    bridge = get_bridge()
    data = {
        "connected": connected,
        "status": bridge.status.name,
        "rc_host": bridge._rc_host,
        "rc_port": bridge._rc_port,
        "python_port": bridge._python_port,
    }
    return ToolResult(
        ok=connected,
        content=f"UE5: {bridge.status.name}",
        data=data,
    )
