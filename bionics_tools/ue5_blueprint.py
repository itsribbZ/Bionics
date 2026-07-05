"""UE5 Blueprint Tools — Graph inspection, node editing, pin connections, interfaces.

Matches soft-ue-cli's blueprint editing surface. These are the deepest,
most valuable UE5 automation tools (~15+ commands in soft-ue-cli).
"""

from __future__ import annotations

from typing import Annotated, Literal

from bionics_tools._ue5_common import escape_path, run_python, wrap_script
from core.bridge import SafetyTier, ToolResult, bionics_tool

# ============================================================================
# QUERY / INSPECT
# ============================================================================


@bionics_tool(
    name="ue5_query_blueprint",
    category="ue5_blueprint",
    safety_tier=SafetyTier.SAFE,
    read_only=True,
    aliases=["query-blueprint"],
    title="Query Blueprint",
)
def ue5_query_blueprint(
    asset_path: Annotated[str, "Path like /Game/Blueprints/BP_Player"],
    include: Annotated[str, "Comma-separated: components, variables, functions, interfaces, all"] = "all",
) -> ToolResult:
    """Inspect a Blueprint — components, variables, functions, interfaces."""
    ap = escape_path(asset_path)
    body = f"""
bp = unreal.load_asset('{ap}')
if bp is None:
    print(_dump({{"error": "blueprint not found: {ap}"}}))
else:
    out = {{"path": '{ap}', "name": bp.get_name(), "class": bp.get_class().get_name()}}
    inc = '{include}'.split(',')
    if 'variables' in inc or 'all' in inc:
        try:
            variables = unreal.BlueprintEditorLibrary.get_variable_names(bp)
            vars_out = []
            for vn in variables:
                vars_out.append(str(vn))
            out['variables'] = vars_out
        except Exception as _ve:
            out['variables_error'] = str(_ve)
    if 'functions' in inc or 'all' in inc:
        try:
            funcs = unreal.BlueprintEditorLibrary.get_function_names(bp)
            out['functions'] = [str(f) for f in funcs]
        except Exception as _e:
            pass  # optional property — graceful degradation
    if 'interfaces' in inc or 'all' in inc:
        try:
            out['interfaces'] = []
            interfaces = bp.get_editor_property('implemented_interfaces') or []
            for iface in interfaces:
                if hasattr(iface, 'interface'):
                    out['interfaces'].append(str(iface.interface.get_name() if iface.interface else 'None'))
        except Exception as _e:
            pass  # optional property — graceful degradation
    if 'components' in inc or 'all' in inc:
        try:
            scs = bp.get_editor_property('simple_construction_script')
            if scs:
                nodes = scs.get_editor_property('all_nodes') or []
                out['components'] = [
                    {{
                        "name": n.get_editor_property('variable_name') if hasattr(n, 'get_editor_property') else str(n),
                        "template": str(n.get_editor_property('component_template')) if hasattr(n, 'get_editor_property') else None,
                    }}
                    for n in nodes
                ]
        except Exception as _e:
            out['components'] = []  # SCS unavailable
    print(_dump(out))
"""
    return run_python(wrap_script(body))


@bionics_tool(
    name="ue5_query_blueprint_graph",
    category="ue5_blueprint",
    safety_tier=SafetyTier.SAFE,
    read_only=True,
    title="Query Blueprint Graph",
)
def ue5_query_blueprint_graph(
    asset_path: str,
    graph_name: Annotated[str, "Graph name (EventGraph, AnimGraph, or function name)"] = "EventGraph",
    include_positions: bool = True,
) -> ToolResult:
    """List nodes in a Blueprint graph with connections."""
    ap = escape_path(asset_path)
    gn = escape_path(graph_name)
    body = f"""
bp = unreal.load_asset('{ap}')
if not bp:
    print(_dump({{"error": "not found"}}))
else:
    # Find target graph — check all three graph collections
    target_graph = None
    try:
        for key in ('ubergraph_pages', 'function_graphs', 'macro_graphs'):
            graphs = bp.get_editor_property(key) or []
            for g in graphs:
                if g.get_name() == '{gn}':
                    target_graph = g
                    break
            if target_graph:
                break
    except Exception as _ge:
        print(_dump({{"error": str(_ge)}}))

    if not target_graph:
        print(_dump({{"error": "graph not found: {gn}"}}))
    else:
        nodes_out = []
        try:
            nodes = target_graph.get_editor_property('nodes') or []
            for n in nodes:
                node_info = {{
                    "name": n.get_name(),
                    "class": n.get_class().get_name(),
                    "guid": str(n.node_guid) if hasattr(n, 'node_guid') else None,
                }}
                if {str(include_positions).lower()}:
                    node_info["x"] = n.node_pos_x if hasattr(n, 'node_pos_x') else 0
                    node_info["y"] = n.node_pos_y if hasattr(n, 'node_pos_y') else 0
                try:
                    pins = n.get_editor_property('pins') or []
                    node_info["pins"] = [
                        {{
                            "name": p.get_name(),
                            "direction": str(p.direction) if hasattr(p, 'direction') else '',
                            "type": str(p.pin_type.pin_category) if hasattr(p, 'pin_type') else '',
                        }}
                        for p in pins
                    ]
                except Exception as _e:
                    node_info["pins"] = []  # pins unavailable on this node type
                nodes_out.append(node_info)
        except Exception as _ne:
            print(_dump({{"error": str(_ne)}}))
        print(_dump({{"graph": '{gn}', "nodes": nodes_out, "count": len(nodes_out)}}))
"""
    return run_python(wrap_script(body))


@bionics_tool(
    name="ue5_list_callables",
    category="ue5_blueprint",
    safety_tier=SafetyTier.SAFE,
    read_only=True,
    title="List Blueprint Callables",
)
def ue5_list_callables(asset_path: str) -> ToolResult:
    """List all event/function graphs callable in a Blueprint."""
    ap = escape_path(asset_path)
    body = f"""
bp = unreal.load_asset('{ap}')
if not bp:
    print(_dump({{"error": "not found"}}))
else:
    result = {{"ubergraph_pages": [], "function_graphs": [], "macro_graphs": []}}
    for key in ['ubergraph_pages', 'function_graphs', 'macro_graphs']:
        try:
            graphs = bp.get_editor_property(key) or []
            result[key] = [g.get_name() for g in graphs]
        except Exception as _e:
            pass  # optional property — graceful degradation
    print(_dump(result))
"""
    return run_python(wrap_script(body))


# ============================================================================
# COMPILE / SAVE
# ============================================================================


@bionics_tool(
    name="ue5_compile_blueprint",
    category="ue5_blueprint",
    safety_tier=SafetyTier.MODERATE,
    aliases=["compile-blueprint"],
    title="Compile Blueprint",
)
def ue5_compile_blueprint(asset_path: str) -> ToolResult:
    """Compile a Blueprint/AnimBlueprint and return errors + warnings.

    T1.A native-first re-route (godspeed 2026-05-15): tries the C++ :8090 bridge
    first (5-20ms, structured errors + warnings, works in packaged builds).
    Falls back to Python remote-exec ONLY when the bridge is unreachable so
    legacy environments without the plugin keep working. See Bionics memory
    `feedback_routing_matrix_correction.md` for the why.
    """
    from bionics_tools.ue5_native import call_bridge_tool
    native = call_bridge_tool("compile_blueprint", {"asset_path": asset_path})
    # Pass through native success OR any real bridge-side error (e.g. asset not found).
    # Only fall back to Python when the bridge itself is unreachable.
    if native.ok or "Bridge unreachable" not in (native.error or ""):
        return native
    ap = escape_path(asset_path)
    body = f"""
bp = unreal.load_asset('{ap}')
if not bp:
    print(_dump({{"error": "not found"}}))
else:
    try:
        unreal.BlueprintEditorLibrary.compile_blueprint(bp)
        print(_dump({{"ok": True, "compiled": '{ap}'}}))
    except Exception as _ce:
        print(_dump({{"error": str(_ce)}}))
"""
    return run_python(wrap_script(body))


# ============================================================================
# VARIABLES / FUNCTIONS
# ============================================================================


@bionics_tool(
    name="ue5_add_variable",
    category="ue5_blueprint",
    safety_tier=SafetyTier.MODERATE,
    title="Add Blueprint Variable",
)
def ue5_add_variable(
    asset_path: str,
    variable_name: str,
    variable_type: Annotated[Literal["bool", "int", "float", "string", "vector", "rotator"], "Variable type"] = "float",
    default_value: str = "",
) -> ToolResult:
    """Add a new variable to a Blueprint."""
    allowed = {"bool", "int", "float", "string", "vector", "rotator"}
    vt = variable_type.lower() if isinstance(variable_type, str) else "float"
    if vt not in allowed:
        return ToolResult.failure(
            f"Invalid variable_type: {variable_type!r}. Must be one of: {sorted(allowed)}"
        )
    ap = escape_path(asset_path)
    vn = escape_path(variable_name)
    body = f"""
bp = unreal.load_asset('{ap}')
if not bp:
    print(_dump({{"error": "not found"}}))
else:
    try:
        # Map friendly names to pin types
        type_map = {{
            'bool': unreal.PinType(pin_category='bool'),
            'int': unreal.PinType(pin_category='int'),
            'float': unreal.PinType(pin_category='float'),
            'string': unreal.PinType(pin_category='string'),
            'vector': unreal.PinType(pin_category='struct', pin_sub_category_object=unreal.Vector.static_class()),
            'rotator': unreal.PinType(pin_category='struct', pin_sub_category_object=unreal.Rotator.static_class()),
        }}
        pin_type = type_map.get('{vt}', type_map['float'])
        unreal.BlueprintEditorLibrary.add_member_variable(bp, '{vn}', pin_type)
        print(_dump({{"ok": True, "added": '{vn}', "type": '{vt}'}}))
    except Exception as _ae:
        print(_dump({{"error": str(_ae)}}))
"""
    return run_python(wrap_script(body))


@bionics_tool(
    name="ue5_remove_variable",
    category="ue5_blueprint",
    safety_tier=SafetyTier.DESTRUCTIVE,
    destructive=True,
    strict=True,
    title="Remove Blueprint Variable",
)
def ue5_remove_variable(asset_path: str, variable_name: str) -> ToolResult:
    """Remove a variable from a Blueprint."""
    ap = escape_path(asset_path)
    vn = escape_path(variable_name)
    body = f"""
bp = unreal.load_asset('{ap}')
if not bp:
    print(_dump({{"error": "not found"}}))
else:
    try:
        unreal.BlueprintEditorLibrary.remove_member_variable(bp, '{vn}')
        print(_dump({{"ok": True, "removed": '{vn}'}}))
    except Exception as _ae:
        print(_dump({{"error": str(_ae)}}))
"""
    return run_python(wrap_script(body))


# ============================================================================
# GRAPH EDITING — Nodes
# ============================================================================


@bionics_tool(
    name="ue5_add_graph_node",
    category="ue5_blueprint",
    safety_tier=SafetyTier.MODERATE,
    strict=True,
    aliases=["add-graph-node"],
    title="Add Graph Node",
)
def ue5_add_graph_node(
    asset_path: str,
    node_class: Annotated[str, "UE5 node class (K2Node_CallFunction, etc.)"],
    graph_name: str = "EventGraph",
    position: list[int] | None = None,
) -> ToolResult:
    """Add a new node to a Blueprint graph."""
    pos = position if position else [0, 0]
    if len(pos) < 2:
        return ToolResult.failure("position must have 2 elements [x, y]")
    try:
        px, py = int(pos[0]), int(pos[1])
    except (TypeError, ValueError):
        return ToolResult.failure("position values must be integers")
    ap = escape_path(asset_path)
    nc = escape_path(node_class)
    gn = escape_path(graph_name)
    body = f"""
bp = unreal.load_asset('{ap}')
if not bp:
    print(_dump({{"error": "not found"}}))
else:
    # Find graph
    target_graph = None
    for key in ['ubergraph_pages', 'function_graphs', 'macro_graphs']:
        graphs = bp.get_editor_property(key) or []
        for g in graphs:
            if g.get_name() == '{gn}':
                target_graph = g
                break
        if target_graph: break
    if not target_graph:
        print(_dump({{"error": "graph not found"}}))
    else:
        try:
            node = unreal.BlueprintEditorLibrary.add_node_to_graph(
                target_graph, '{nc}', {px}, {py}
            )
            print(_dump({{"ok": True, "name": node.get_name(), "guid": str(node.node_guid)}}))
        except Exception as _ne:
            print(_dump({{"error": str(_ne)}}))
"""
    return run_python(wrap_script(body))


@bionics_tool(
    name="ue5_remove_graph_node",
    category="ue5_blueprint",
    safety_tier=SafetyTier.DESTRUCTIVE,
    destructive=True,
    strict=True,
    title="Remove Graph Node",
)
def ue5_remove_graph_node(asset_path: str, node_guid: str) -> ToolResult:
    """Remove a node from a Blueprint graph by GUID."""
    ap = escape_path(asset_path)
    ng = escape_path(node_guid)
    body = f"""
bp = unreal.load_asset('{ap}')
if not bp:
    print(_dump({{"error": "not found"}}))
else:
    found = False
    for key in ['ubergraph_pages', 'function_graphs', 'macro_graphs']:
        graphs = bp.get_editor_property(key) or []
        for g in graphs:
            nodes = g.get_editor_property('nodes') or []
            for n in nodes:
                if str(n.node_guid) == '{ng}':
                    try:
                        unreal.BlueprintEditorLibrary.remove_node(n)
                        found = True
                    except Exception as _re:
                        print(_dump({{"error": str(_re)}}))
                        raise RuntimeError(str(_re))
                    break
            if found: break
        if found: break
    print(_dump({{"ok": found, "removed_guid": '{ng}'}}))
"""
    return run_python(wrap_script(body))


@bionics_tool(
    name="ue5_connect_pins",
    category="ue5_blueprint",
    safety_tier=SafetyTier.MODERATE,
    strict=True,
    aliases=["connect-graph-pins"],
    title="Connect Pins",
)
def ue5_connect_pins(
    asset_path: str,
    source_node: Annotated[str, "Source node name or GUID"],
    source_pin: str,
    target_node: Annotated[str, "Target node name or GUID"],
    target_pin: str,
) -> ToolResult:
    """Connect two pins in a Blueprint graph."""
    ap = escape_path(asset_path)
    sn = escape_path(source_node)
    sp = escape_path(source_pin)
    tn = escape_path(target_node)
    tp = escape_path(target_pin)
    body = f"""
bp = unreal.load_asset('{ap}')
if not bp:
    print(_dump({{"error": "not found"}}))
else:
    source = None
    target = None
    for key in ['ubergraph_pages', 'function_graphs']:
        graphs = bp.get_editor_property(key) or []
        for g in graphs:
            for n in (g.get_editor_property('nodes') or []):
                if n.get_name() == '{sn}' or str(n.node_guid) == '{sn}':
                    source = n
                if n.get_name() == '{tn}' or str(n.node_guid) == '{tn}':
                    target = n
    if not source or not target:
        print(_dump({{"error": "node(s) not found", "source_found": source is not None, "target_found": target is not None}}))
    else:
        src_pin = None
        tgt_pin = None
        for p in (source.get_editor_property('pins') or []):
            if p.get_name() == '{sp}':
                src_pin = p; break
        for p in (target.get_editor_property('pins') or []):
            if p.get_name() == '{tp}':
                tgt_pin = p; break
        if not src_pin or not tgt_pin:
            print(_dump({{"error": "pin(s) not found"}}))
        else:
            try:
                src_pin.make_link_to(tgt_pin)
                unreal.BlueprintEditorLibrary.compile_blueprint(bp)
                print(_dump({{"ok": True, "connected": True}}))
            except Exception as _ce:
                try:
                    src_pin.break_all_pin_links()
                except Exception as _rb:
                    pass  # rollback best-effort
                print(_dump({{"error": str(_ce), "note": "link rolled back on compile failure"}}))
"""
    return run_python(wrap_script(body))


@bionics_tool(
    name="ue5_disconnect_pin",
    category="ue5_blueprint",
    safety_tier=SafetyTier.MODERATE,
    strict=True,
    title="Disconnect Pin",
)
def ue5_disconnect_pin(
    asset_path: str,
    node_name: str,
    pin_name: str,
) -> ToolResult:
    """Disconnect all links from a pin."""
    ap = escape_path(asset_path)
    nn = escape_path(node_name)
    pn = escape_path(pin_name)
    body = f"""
bp = unreal.load_asset('{ap}')
if not bp:
    print(_dump({{"error": "not found"}}))
else:
    found = False
    for key in ['ubergraph_pages', 'function_graphs']:
        for g in (bp.get_editor_property(key) or []):
            for n in (g.get_editor_property('nodes') or []):
                if n.get_name() == '{nn}' or str(n.node_guid) == '{nn}':
                    for p in (n.get_editor_property('pins') or []):
                        if p.get_name() == '{pn}':
                            p.break_all_pin_links()
                            found = True
                            break
                    break
    print(_dump({{"ok": found, "disconnected": '{pn}'}}))
"""
    return run_python(wrap_script(body))


@bionics_tool(
    name="ue5_set_node_position",
    category="ue5_blueprint",
    safety_tier=SafetyTier.SAFE,
    title="Set Node Position",
)
def ue5_set_node_position(
    asset_path: str,
    node_guid: str,
    x: int,
    y: int,
) -> ToolResult:
    """Set the position of a graph node."""
    ap = escape_path(asset_path)
    ng = escape_path(node_guid)
    body = f"""
bp = unreal.load_asset('{ap}')
if not bp:
    print(_dump({{"error": "not found"}}))
else:
    done = False
    for key in ['ubergraph_pages', 'function_graphs']:
        for g in (bp.get_editor_property(key) or []):
            for n in (g.get_editor_property('nodes') or []):
                if str(n.node_guid) == '{ng}' or n.get_name() == '{ng}':
                    n.node_pos_x = {x}
                    n.node_pos_y = {y}
                    done = True
                    break
    print(_dump({{"ok": done, "x": {x}, "y": {y}}}))
"""
    return run_python(wrap_script(body))


# ============================================================================
# INTERFACES
# ============================================================================


@bionics_tool(
    name="ue5_add_interface",
    category="ue5_blueprint",
    safety_tier=SafetyTier.MODERATE,
    title="Add Blueprint Interface",
)
def ue5_add_interface(asset_path: str, interface_path: str) -> ToolResult:
    """Add a Blueprint interface to an asset."""
    ap = escape_path(asset_path)
    ip = escape_path(interface_path)
    body = f"""
bp = unreal.load_asset('{ap}')
if not bp:
    print(_dump({{"error": "not found"}}))
else:
    try:
        unreal.BlueprintEditorLibrary.add_interface(bp, '{ip}')
        print(_dump({{"ok": True, "added": '{ip}'}}))
    except Exception as _ae:
        print(_dump({{"error": str(_ae)}}))
"""
    return run_python(wrap_script(body))


@bionics_tool(
    name="ue5_remove_interface",
    category="ue5_blueprint",
    safety_tier=SafetyTier.DESTRUCTIVE,
    destructive=True,
    title="Remove Blueprint Interface",
)
def ue5_remove_interface(asset_path: str, interface_path: str) -> ToolResult:
    """Remove a Blueprint interface from an asset."""
    ap = escape_path(asset_path)
    ip = escape_path(interface_path)
    body = f"""
bp = unreal.load_asset('{ap}')
if not bp:
    print(_dump({{"error": "not found"}}))
else:
    try:
        unreal.BlueprintEditorLibrary.remove_interface(bp, '{ip}')
        print(_dump({{"ok": True, "removed": '{ip}'}}))
    except Exception as _ae:
        print(_dump({{"error": str(_ae)}}))
"""
    return run_python(wrap_script(body))


# ============================================================================
# ANIM BLUEPRINT HELPERS
# ============================================================================


@bionics_tool(
    name="ue5_anim_graph_nodes",
    category="ue5_blueprint",
    safety_tier=SafetyTier.SAFE,
    read_only=True,
    title="List AnimGraph Nodes",
)
def ue5_anim_graph_nodes(asset_path: str) -> ToolResult:
    """List all nodes in an Animation Blueprint's AnimGraph."""
    ap = escape_path(asset_path)
    body = f"""
ab = unreal.load_asset('{ap}')
if not ab:
    print(_dump({{"error": "not found"}}))
else:
    try:
        nodes = unreal.AnimationBlueprintLibrary.get_anim_graph_nodes(ab)
        result = [
            {{"name": n.get_name(), "class": n.get_class().get_name()}}
            for n in nodes
        ]
        print(_dump({{"nodes": result, "count": len(result)}}))
    except Exception as _ne:
        print(_dump({{"error": str(_ne)}}))
"""
    return run_python(wrap_script(body))


# ============================================================================
# FIND REFERENCES
# ============================================================================


@bionics_tool(
    name="ue5_find_references",
    category="ue5_blueprint",
    safety_tier=SafetyTier.SAFE,
    read_only=True,
    title="Find References",
)
def ue5_find_references(asset_path: str) -> ToolResult:
    """Find all assets that reference the given asset."""
    ap = escape_path(asset_path)
    body = f"""
asset = unreal.load_asset('{ap}')
if not asset:
    print(_dump({{"error": "not found"}}))
else:
    try:
        ar = unreal.AssetRegistryHelpers.get_asset_registry()
        refs = ar.get_referencers(asset.get_path_name(), unreal.AssetRegistryDependencyOptions())
        print(_dump({{"referencers": [str(r) for r in refs], "count": len(refs)}}))
    except Exception as _re:
        print(_dump({{"error": str(_re)}}))
"""
    return run_python(wrap_script(body))
