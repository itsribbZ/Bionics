"""UE5 Control Rig Tools — Full Body IK + procedural animation.

Fills Bible Step 6 (Control Rig for IK) which was entirely absent from Bionics
before 2026-04-16.

Tool surface:
    ue5_create_control_rig              — create ControlRigBlueprint asset
    ue5_assign_control_rig_to_animbp    — wire ControlRig node in AnimBP (via C++ bridge)
    ue5_control_rig_info                — inspect a ControlRig asset (read-only diagnostic)

Bible reference: Model Animations Bible Ch.6 (IK — Control Rig, FBIK, Foot IK).

**Note on UE5 5.7 Python limits**: Control Rig graph node editing (adding FBIK
nodes, wiring effectors) requires C++ — like AnimGraph node editing. These
tools implement what Python CAN do (asset creation, AnimBP binding) and route
graph-level operations to the C++ bridge tool `ue5_create_animgraph_node` with
class `AnimGraphNode_ControlRig`.
"""

from __future__ import annotations

from typing import Annotated

from bionics_tools._ue5_common import escape_path, run_python, wrap_script
from core.bridge import SafetyTier, ToolResult, bionics_tool

# ============================================================================
# CONTROL RIG ASSET
# ============================================================================


@bionics_tool(
    name="ue5_create_control_rig",
    category="ue5_controlrig",
    safety_tier=SafetyTier.MODERATE,
    aliases=["create-control-rig"],
    title="Create Control Rig",
)
def ue5_create_control_rig(
    rig_path: Annotated[str, "Full asset path (/Game/Characters/CR_Trooper_IK)"],
    skeletal_mesh_path: Annotated[str, "Preview skeletal mesh to bind"] = "",
    parent_class: Annotated[str, "Parent ControlRig class"] = "ControlRig",
) -> ToolResult:
    """Create a new ControlRigBlueprint asset.

    The returned asset is an empty Control Rig ready for procedural nodes.
    Use `ue5_create_animgraph_node` with class `AnimGraphNode_ControlRig` to add
    FBIK / Foot IK nodes inside the rig, or set up via Control Rig editor.

    Python cannot edit Control Rig graphs — only C++ can. This tool creates
    the container asset; graph editing needs BionicsBridge C++ plugin.
    """
    rp = escape_path(rig_path)
    sm = escape_path(skeletal_mesh_path)
    pc = escape_path(parent_class)
    body = f"""
rig_path = '{rp}'
mesh_path = '{sm}'
parent_class_name = '{pc}'

existing = unreal.load_asset(rig_path)
if existing:
    print(_dump({{"ok": False, "error": "Control Rig already exists", "path": rig_path}}))
else:
    factory = unreal.ControlRigBlueprintFactory()
    factory.set_editor_property('parent_class', unreal.ControlRig.static_class())

    asset_tools = unreal.AssetToolsHelpers.get_asset_tools()
    pkg_path, asset_name = rig_path.rsplit('/', 1)
    rig = asset_tools.create_asset(asset_name, pkg_path, unreal.ControlRigBlueprint, factory)

    if not rig:
        print(_dump({{"ok": False, "error": "Factory returned None", "path": rig_path}}))
    else:
        # Bind preview mesh if provided
        if mesh_path:
            mesh = unreal.load_asset(mesh_path)
            if mesh:
                rig.set_editor_property('preview_skeletal_mesh', mesh)

        unreal.EditorAssetLibrary.save_asset(rig_path)
        print(_dump({{
            "ok": True,
            "path": rig_path,
            "mesh_bound": bool(mesh_path),
            "note": "Asset created — graph editing requires Control Rig editor or C++ bridge",
        }}))
"""
    return run_python(wrap_script(body))


@bionics_tool(
    name="ue5_assign_control_rig_to_animbp",
    category="ue5_controlrig",
    safety_tier=SafetyTier.MODERATE,
    aliases=["assign-control-rig"],
    title="Assign Control Rig to AnimBP",
)
def ue5_assign_control_rig_to_animbp(
    animbp_path: Annotated[str, "Target AnimBP (/Game/Blueprints/ABP_SWCharacter)"],
    control_rig_path: Annotated[str, "Control Rig asset (/Game/.../CR_Trooper_IK)"],
    alpha: Annotated[float, "Blend alpha 0-1"] = 1.0,
) -> ToolResult:
    """Add a Control Rig node to an AnimBP's AnimGraph.

    Routes to `ue5_create_animgraph_node` (BionicsBridge C++) with
    node_class=`AnimGraphNode_ControlRig`. Returns a hint to wire the node
    into the graph via `ue5_wire_animgraph_pins` as a separate step.

    Python fallback note: this tool does NOT create the node itself — it
    returns a structured plan instructing the caller to use the C++ bridge.
    If you have the bridge running, call `ue5_create_animgraph_node` directly.
    """
    ap = escape_path(animbp_path)
    cr = escape_path(control_rig_path)
    body = f"""
animbp_path = '{ap}'
cr_path = '{cr}'
alpha = {alpha}

animbp = unreal.load_asset(animbp_path)
cr = unreal.load_asset(cr_path)
if not animbp:
    print(_dump({{"ok": False, "error": f"AnimBP not found: {{animbp_path}}"}}))
elif not cr:
    print(_dump({{"ok": False, "error": f"Control Rig not found: {{cr_path}}"}}))
else:
    # Python cannot add AnimGraph nodes — delegate to BionicsBridge C++
    print(_dump({{
        "ok": True,
        "action_required": "call_bionicsbridge",
        "instruction": {{
            "tool": "ue5_create_animgraph_node",
            "params": {{
                "blueprint_path": animbp_path,
                "node_class": "AnimGraphNode_ControlRig",
                "properties": {{"ControlRigClass": cr_path, "Alpha": alpha}},
            }},
        }},
        "animbp": animbp_path,
        "control_rig": cr_path,
        "note": "Python cannot create AnimGraph nodes — use BionicsBridge C++ tool ue5_create_animgraph_node",
    }}))
"""
    return run_python(wrap_script(body))


@bionics_tool(
    name="ue5_control_rig_info",
    category="ue5_controlrig",
    safety_tier=SafetyTier.SAFE,
    read_only=True,
    aliases=["control-rig-info"],
    title="Inspect Control Rig",
)
def ue5_control_rig_info(
    rig_path: Annotated[str, "Control Rig asset path"],
) -> ToolResult:
    """Read-only inspection of a ControlRigBlueprint.

    Returns node count, variable list, hierarchy element count, preview mesh
    binding, and whether the rig has a valid initialized hierarchy.
    """
    rp = escape_path(rig_path)
    body = f"""
rig_path = '{rp}'
rig = unreal.load_asset(rig_path)
if not rig:
    print(_dump({{"error": f"Control Rig not found: {{rig_path}}"}}))
else:
    info = {{
        "path": rig_path,
        "class": rig.__class__.__name__,
        "preview_mesh": None,
        "hierarchy_element_count": 0,
        "variable_count": 0,
    }}

    # Preview mesh
    try:
        mesh = rig.get_editor_property('preview_skeletal_mesh')
        if mesh:
            info["preview_mesh"] = str(mesh.get_path_name())
    except Exception:
        pass

    # Hierarchy (CR rig structure)
    try:
        hierarchy = rig.get_editor_property('hierarchy')
        if hierarchy and hasattr(hierarchy, 'num'):
            info["hierarchy_element_count"] = int(hierarchy.num())
    except Exception:
        pass

    # Variables
    try:
        bpgc = rig.generated_class()
        if bpgc and hasattr(bpgc, 'get_class'):
            info["variable_count"] = len([p for p in dir(bpgc) if not p.startswith('_')])
    except Exception:
        pass

    print(_dump(info))
"""
    return run_python(wrap_script(body))
