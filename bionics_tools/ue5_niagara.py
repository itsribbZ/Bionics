"""UE5 Niagara Tools — VFX spawn + parameter binding.

Closes the Niagara VFX gap flagged 2026-04-17 audit. Before this module,
every NS_Explosion/projectile trail/atmosphere VFX was dropped into the
level manually. These two tools let Bionics spawn Niagara systems at world
coords (or attached to actors) and set User Exposed Parameters from code.

Tool surface:
    ue5_niagara_spawn_emitter   — spawn NiagaraSystem at location (world/attached)
    ue5_niagara_set_param       — set User Exposed Parameter (float/vec3/bool/int)

Bible reference: Combat Arena hit/death FX, Cymatics atmosphere emitters.
UE5 5.1+ required (UNiagaraFunctionLibrary Python bindings).
"""

from __future__ import annotations

from typing import Annotated, Literal

from bionics_tools._ue5_common import escape_path, run_python, wrap_script
from core.bridge import SafetyTier, ToolResult, bionics_tool


@bionics_tool(
    name="ue5_niagara_spawn_emitter",
    category="ue5_niagara",
    safety_tier=SafetyTier.MODERATE,
    strict=True,
    aliases=["spawn-niagara", "niagara-spawn"],
    title="Spawn Niagara Emitter",
)
def ue5_niagara_spawn_emitter(
    system_path: Annotated[str, "NiagaraSystem asset path (/Game/VFX/NS_Explosion)"],
    location: Annotated[list[float], "[x, y, z] world location"],
    rotation: Annotated[list[float] | None, "[pitch, yaw, roll]"] = None,
    scale: Annotated[float, "Uniform scale factor (1.0 = default)"] = 1.0,
    attach_actor_label: Annotated[str, "Attach to actor by label (empty = world-space)"] = "",
    auto_destroy: Annotated[bool, "Destroy component when effect completes"] = True,
) -> ToolResult:
    """Spawn a Niagara system at a world location (or attached to an actor).

    Use for projectile impacts, explosions, atmosphere effects, ability VFX.
    When attach_actor_label is set, the emitter spawns attached to that actor
    at the specified relative offset — useful for combat hit sparks and
    weapon muzzle flashes.
    """
    if not isinstance(location, list) or len(location) != 3:
        return ToolResult.failure("location must be [x, y, z]")
    try:
        lx, ly, lz = float(location[0]), float(location[1]), float(location[2])
    except (TypeError, ValueError):
        return ToolResult.failure("location values must be numeric")
    rot = rotation if rotation else [0.0, 0.0, 0.0]
    if len(rot) != 3:
        return ToolResult.failure("rotation must be [pitch, yaw, roll]")
    try:
        rp, ry, rr = float(rot[0]), float(rot[1]), float(rot[2])
    except (TypeError, ValueError):
        return ToolResult.failure("rotation values must be numeric")
    sp = escape_path(system_path)
    al = escape_path(attach_actor_label)
    auto = "True" if auto_destroy else "False"
    body = f"""
sys_path = '{sp}'
ns = unreal.load_asset(sys_path)
if not ns:
    print(_dump({{"ok": False, "error": f"NiagaraSystem not found: {{sys_path}}"}}))
else:
    world = unreal.EditorLevelLibrary.get_editor_world()
    if not world:
        print(_dump({{"ok": False, "error": "no editor world"}}))
    else:
        loc = unreal.Vector({lx}, {ly}, {lz})
        rot = unreal.Rotator({rp}, {ry}, {rr})
        attach_label = '{al}'
        component = None
        if attach_label:
            # Attach to actor by label
            actors = unreal.EditorLevelLibrary.get_all_level_actors()
            target = next((a for a in actors if a.get_actor_label() == attach_label), None)
            if not target:
                print(_dump({{"ok": False, "error": f"actor not found: {{attach_label}}"}}))
            else:
                component = unreal.NiagaraFunctionLibrary.spawn_system_attached(
                    ns, target.root_component,
                    unreal.Name('None'),
                    loc, rot,
                    unreal.EAttachLocation.KEEP_RELATIVE_OFFSET,
                    {auto},
                )
        else:
            component = unreal.NiagaraFunctionLibrary.spawn_system_at_location(
                world, ns, loc, rot,
                unreal.Vector({scale}, {scale}, {scale}),
                {auto},
            )
        if component:
            print(_dump({{
                "ok": True,
                "system": sys_path,
                "location": [{lx}, {ly}, {lz}],
                "attached_to": attach_label or None,
                "component": component.get_name(),
            }}))
        else:
            print(_dump({{"ok": False, "error": "spawn returned None"}}))
"""
    return run_python(wrap_script(body))


@bionics_tool(
    name="ue5_niagara_set_param",
    category="ue5_niagara",
    safety_tier=SafetyTier.MODERATE,
    strict=True,
    aliases=["niagara-param", "set-niagara-variable"],
    title="Set Niagara Parameter",
)
def ue5_niagara_set_param(
    component_name: Annotated[str, "NiagaraComponent name (from spawn_emitter), or actor label with NS component"],
    parameter_name: Annotated[str, "User Exposed Parameter name (User.ExplosionRadius, User.Color)"],
    param_type: Annotated[Literal["float", "int", "bool", "vector", "color"], "Parameter type"],
    value: Annotated[str, "Float/int/bool as string, or comma-separated '1.0,0.5,0.0' for vector/color"],
) -> ToolResult:
    """Set a User Exposed Parameter on a live NiagaraComponent.

    For params prefixed `User.`, the engine finds them in the system's exposed
    namespace. Cymatics frequency→radius bindings, per-explosion color tints,
    and ability-scaled emission rates all flow through this tool.
    """
    cn = escape_path(component_name)
    pn = escape_path(parameter_name)
    pv = escape_path(value)

    if param_type in ("vector", "color"):
        # Parse comma-separated floats
        try:
            parts = [float(p.strip()) for p in value.split(",")]
        except ValueError:
            return ToolResult.failure(f"'{value}' is not a comma-separated float list for {param_type}")
        if param_type == "vector" and len(parts) != 3:
            return ToolResult.failure("vector requires 3 comma-separated floats")
        if param_type == "color" and len(parts) not in (3, 4):
            return ToolResult.failure("color requires 3 or 4 comma-separated floats")

    body = f"""
comp_name = '{cn}'
param_name = '{pn}'
ptype = '{param_type}'
raw_value = '{pv}'

# Find component — search live Niagara components in level
target_comp = None
world = unreal.EditorLevelLibrary.get_editor_world()
if world:
    actors = unreal.EditorLevelLibrary.get_all_level_actors()
    for a in actors:
        if a.get_actor_label() == comp_name or a.get_name() == comp_name:
            # Actor match — look for Niagara component
            for c in a.get_components_by_class(unreal.NiagaraComponent):
                target_comp = c
                break
            if target_comp: break
        for c in a.get_components_by_class(unreal.NiagaraComponent):
            if c.get_name() == comp_name:
                target_comp = c
                break
        if target_comp: break

if not target_comp:
    print(_dump({{"ok": False, "error": f"NiagaraComponent not found: {{comp_name}}"}}))
else:
    try:
        if ptype == 'float':
            target_comp.set_variable_float(unreal.Name(param_name), float(raw_value))
        elif ptype == 'int':
            target_comp.set_variable_int(unreal.Name(param_name), int(raw_value))
        elif ptype == 'bool':
            target_comp.set_variable_bool(unreal.Name(param_name), raw_value.lower() in ('true', '1', 'yes'))
        elif ptype == 'vector':
            vals = [float(p.strip()) for p in raw_value.split(',')]
            target_comp.set_variable_vec3(unreal.Name(param_name), unreal.Vector(vals[0], vals[1], vals[2]))
        elif ptype == 'color':
            vals = [float(p.strip()) for p in raw_value.split(',')]
            r, g, b = vals[0], vals[1], vals[2]
            a_val = vals[3] if len(vals) > 3 else 1.0
            target_comp.set_variable_linear_color(unreal.Name(param_name), unreal.LinearColor(r, g, b, a_val))
        print(_dump({{"ok": True, "component": comp_name, "parameter": param_name, "type": ptype}}))
    except Exception as _pe:
        print(_dump({{"ok": False, "error": str(_pe)}}))
"""
    return run_python(wrap_script(body))
