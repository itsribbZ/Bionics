"""UE5 AnimGraph Tools — Full AnimBP automation via BionicsBridge C++ plugin.

These tools call the C++ BionicsBridge plugin's AnimGraph manipulation
endpoints. Unlike Python Remote Execution (which CANNOT wire AnimGraph
nodes), these use native UE5 C++ APIs for full graph control:

    - Query entire AnimGraph structure (nodes, pins, connections)
    - Create any AnimGraph node type
    - Wire/unwire pins programmatically
    - Delete nodes with undo support
    - Set node properties (animation sequences, blend spaces, slot names)
    - Create state machines with states
    - Add state transitions with conditions

This is THE competitive moat — no other tool in the market can do this.
"""

from __future__ import annotations

from typing import Annotated

from bionics_tools.ue5_native import call_bridge_tool as _call_tool
from core.bridge import SafetyTier, ToolResult, bionics_tool

# ============================================================================
# QUERY
# ============================================================================


@bionics_tool(
    name="ue5_query_animgraph",
    category="ue5_animgraph",
    safety_tier=SafetyTier.SAFE,
    read_only=True,
    aliases=["query-animgraph", "get-animgraph"],
    title="Query AnimGraph",
)
def ue5_query_animgraph(
    asset_path: Annotated[str, "AnimBP path like /Game/Blueprints/ABP_Character"],
    include_hidden_pins: Annotated[bool, "Include hidden pins in output"] = False,
) -> ToolResult:
    """Get full AnimGraph structure: all nodes, pins, connections, state machines."""
    return _call_tool("query_animgraph", {
        "asset_path": asset_path,
        "include_hidden_pins": include_hidden_pins,
    })


# ============================================================================
# CREATE
# ============================================================================


@bionics_tool(
    name="ue5_create_animgraph_node",
    category="ue5_animgraph",
    safety_tier=SafetyTier.MODERATE,
    read_only=False,
    aliases=["create-animgraph-node", "add-animgraph-node"],
    title="Create AnimGraph Node",
)
def ue5_create_animgraph_node(
    asset_path: Annotated[str, "AnimBP path like /Game/Blueprints/ABP_Character"],
    node_class: Annotated[str, "Node class: AnimGraphNode_SequencePlayer, AnimGraphNode_BlendSpacePlayer, "
                                "AnimGraphNode_Slot, AnimGraphNode_StateMachine, AnimGraphNode_LayeredBoneBlend, "
                                "AnimGraphNode_BlendListByBool, AnimGraphNode_TwoWayBlend, "
                                "AnimGraphNode_SaveCachedPose, AnimGraphNode_UseCachedPose, "
                                "AnimGraphNode_LinkedAnimLayer"],
    pos_x: Annotated[int, "X position in graph"] = 0,
    pos_y: Annotated[int, "Y position in graph"] = 0,
) -> ToolResult:
    """Create a new node in the AnimGraph. Returns node name and pin list."""
    return _call_tool("create_animgraph_node", {
        "asset_path": asset_path,
        "node_class": node_class,
        "pos_x": pos_x,
        "pos_y": pos_y,
    })


# ============================================================================
# WIRE / UNWIRE
# ============================================================================


@bionics_tool(
    name="ue5_wire_animgraph_pins",
    category="ue5_animgraph",
    safety_tier=SafetyTier.MODERATE,
    read_only=False,
    strict=True,
    aliases=["wire-animgraph", "connect-animgraph-pins"],
    title="Wire AnimGraph Pins",
)
def ue5_wire_animgraph_pins(
    asset_path: Annotated[str, "AnimBP path"],
    source_node: Annotated[str, "Source node name (from query_animgraph)"],
    source_pin: Annotated[str, "Source output pin name"],
    target_node: Annotated[str, "Target node name"],
    target_pin: Annotated[str, "Target input pin name"],
    auto_compile: Annotated[bool, "Auto-compile after wiring"] = True,
) -> ToolResult:
    """Connect two AnimGraph pins. Schema-validated, auto-compiles."""
    return _call_tool("wire_animgraph_pins", {
        "asset_path": asset_path,
        "source_node": source_node,
        "source_pin": source_pin,
        "target_node": target_node,
        "target_pin": target_pin,
        "auto_compile": auto_compile,
    })


@bionics_tool(
    name="ue5_unwire_animgraph_pins",
    category="ue5_animgraph",
    safety_tier=SafetyTier.MODERATE,
    read_only=False,
    aliases=["unwire-animgraph", "disconnect-animgraph-pins"],
    title="Unwire AnimGraph Pins",
)
def ue5_unwire_animgraph_pins(
    asset_path: Annotated[str, "AnimBP path"],
    node_name: Annotated[str, "Node name"],
    pin_name: Annotated[str, "Pin to disconnect"],
    target_node: Annotated[str, "Specific target node (omit to break all)"] = "",
    target_pin: Annotated[str, "Specific target pin (omit to break all)"] = "",
) -> ToolResult:
    """Disconnect AnimGraph pins. Break all or specific connection."""
    return _call_tool("unwire_animgraph_pins", {
        "asset_path": asset_path,
        "node_name": node_name,
        "pin_name": pin_name,
        "target_node": target_node,
        "target_pin": target_pin,
    })


# ============================================================================
# DELETE
# ============================================================================


@bionics_tool(
    name="ue5_delete_animgraph_node",
    category="ue5_animgraph",
    safety_tier=SafetyTier.DESTRUCTIVE,
    destructive=True,
    read_only=False,
    strict=True,
    aliases=["delete-animgraph-node", "remove-animgraph-node"],
    title="Delete AnimGraph Node",
)
def ue5_delete_animgraph_node(
    asset_path: Annotated[str, "AnimBP path"],
    node_name: Annotated[str, "Node to delete (cannot delete Output Pose)"],
) -> ToolResult:
    """Delete a node from the AnimGraph. Breaks all connections first."""
    return _call_tool("delete_animgraph_node", {
        "asset_path": asset_path,
        "node_name": node_name,
    })


# ============================================================================
# PROPERTIES
# ============================================================================


@bionics_tool(
    name="ue5_set_animnode_property",
    category="ue5_animgraph",
    safety_tier=SafetyTier.MODERATE,
    read_only=False,
    strict=True,
    aliases=["set-animnode-property", "set-animgraph-property"],
    title="Set AnimNode Property",
)
def ue5_set_animnode_property(
    asset_path: Annotated[str, "AnimBP path"],
    node_name: Annotated[str, "Node name"],
    property_name: Annotated[str, "Property: Sequence, BlendSpace, SlotName, or any UPROPERTY name"],
    property_value: Annotated[str, "Value: asset path for Sequence/BlendSpace, string for SlotName"],
) -> ToolResult:
    """Set a property on an AnimGraph node (anim sequence, blend space, slot name, etc.)."""
    return _call_tool("set_animnode_property", {
        "asset_path": asset_path,
        "node_name": node_name,
        "property_name": property_name,
        "property_value": property_value,
    })


# ============================================================================
# STATE MACHINES
# ============================================================================


@bionics_tool(
    name="ue5_create_state_machine",
    category="ue5_animgraph",
    safety_tier=SafetyTier.MODERATE,
    read_only=False,
    aliases=["create-state-machine"],
    title="Create State Machine",
)
def ue5_create_state_machine(
    asset_path: Annotated[str, "AnimBP path"],
    state_names: Annotated[list[str], "List of state names (first = entry state)"],
    machine_name: Annotated[str, "State machine name"] = "Locomotion",
    pos_x: Annotated[int, "X position"] = -200,
    pos_y: Annotated[int, "Y position"] = 0,
) -> ToolResult:
    """Create a state machine with named states. First state is auto-wired as entry."""
    return _call_tool("create_state_machine", {
        "asset_path": asset_path,
        "state_names": state_names,
        "machine_name": machine_name,
        "pos_x": pos_x,
        "pos_y": pos_y,
    })


@bionics_tool(
    name="ue5_add_state_transition",
    category="ue5_animgraph",
    safety_tier=SafetyTier.MODERATE,
    read_only=False,
    strict=True,
    aliases=["add-state-transition"],
    title="Add State Transition",
)
def ue5_add_state_transition(
    asset_path: Annotated[str, "AnimBP path"],
    state_machine_node: Annotated[str, "State machine node name (from create_state_machine)"],
    source_state: Annotated[str, "Source state name"],
    target_state: Annotated[str, "Target state name"],
    condition_variable: Annotated[str, "Boolean variable name for transition rule"] = "",
) -> ToolResult:
    """Add a transition between states with optional condition variable."""
    return _call_tool("add_state_transition", {
        "asset_path": asset_path,
        "state_machine_node": state_machine_node,
        "source_state": source_state,
        "target_state": target_state,
        "condition_variable": condition_variable,
    })


# ============================================================================
# T-BRIDGE-1 WIRING TOOLS — close the manual-editor handoff gap
# ============================================================================


@bionics_tool(
    name="ue5_set_bone_reference",
    category="ue5_animgraph",
    safety_tier=SafetyTier.MODERATE,
    read_only=False,
    strict=True,
    aliases=["set-bone-reference", "set-bone-ref"],
    title="Set Bone Reference",
)
def ue5_set_bone_reference(
    asset_path: Annotated[str, "AnimBP path"],
    node_name: Annotated[str, "AnimGraph node name (e.g. AnimGraphNode_ModifyBone_0)"],
    bone_name: Annotated[str, "Bone name on the AnimBP's TargetSkeleton"],
    bone_property: Annotated[str, "FBoneReference field on inner FAnimNode"] = "BoneToModify",
) -> ToolResult:
    """Set an FBoneReference field on an AnimGraph node, resolving MeshBoneIndex
    against the AnimBP's TargetSkeleton.

    Closes T-BRIDGE-1 hole #1. Common targets:
      - ModifyBone.BoneToModify (default)
      - TwoBoneIK.IKBone, TwoBoneIK.JointTargetLocationBone
      - SkeletalControl variants with FBoneReference fields

    Returns bone_index resolved on the skeleton — INDEX_NONE means the bone
    name isn't on this skeleton (bridge surfaces a clean error).
    """
    return _call_tool("set_bone_reference", {
        "asset_path": asset_path,
        "node_name": node_name,
        "bone_property": bone_property,
        "bone_name": bone_name,
    })


@bionics_tool(
    name="ue5_bind_pin_to_property",
    category="ue5_animgraph",
    safety_tier=SafetyTier.MODERATE,
    read_only=False,
    strict=True,
    aliases=["bind-pin", "bind-pin-to-property"],
    title="Bind Pin to Property",
)
def ue5_bind_pin_to_property(
    asset_path: Annotated[str, "AnimBP path"],
    node_name: Annotated[str, "AnimGraph node name"],
    pin_name: Annotated[str, "Input pin name to bind (e.g. 'bIsCrouched', 'Alpha')"],
    source_variable: Annotated[str, "AnimInstance member variable name"],
) -> ToolResult:
    """Bind an AnimGraph node input pin to a UAnimInstance member variable —
    the editor "right-click pin → Bind to <variable>" action.

    Closes T-BRIDGE-1 hole #2. Verifies the variable exists on the AnimBP class
    hierarchy before binding (fails clean if missing). Implementation uses
    UAnimGraphNode_Base::PropertyBindings (PropertyAccess type).
    """
    return _call_tool("bind_pin_to_property", {
        "asset_path": asset_path,
        "node_name": node_name,
        "pin_name": pin_name,
        "source_variable": source_variable,
    })


@bionics_tool(
    name="ue5_splice_pose_flow",
    category="ue5_animgraph",
    safety_tier=SafetyTier.MODERATE,
    read_only=False,
    strict=True,
    aliases=["splice-pose", "splice-pose-flow"],
    title="Splice Pose Flow",
)
def ue5_splice_pose_flow(
    asset_path: Annotated[str, "AnimBP path"],
    source_node: Annotated[str, "Existing source node name (output side)"],
    source_pin: Annotated[str, "Output pin on source node"],
    sink_node: Annotated[str, "Existing sink node name (input side)"],
    sink_pin: Annotated[str, "Input pin on sink node"],
    splice_node: Annotated[str, "New node to insert"],
    splice_input_pin: Annotated[str, "Input pin on splice node (receives source)"],
    splice_output_pin: Annotated[str, "Output pin on splice node (drives sink)"],
) -> ToolResult:
    """Atomic insert: break existing source→sink wire (if any), then wire
    source→splice.in and splice.out→sink.

    Closes T-BRIDGE-1 hole #3. Returns broke_existing_wire (false = first-time
    wiring, no break needed). Use when adding LayeredBlendPerBone, Inertialization,
    or any pose-modifying node into an existing chain (e.g. UpperBody slot fix).
    """
    return _call_tool("splice_pose_flow", {
        "asset_path": asset_path,
        "source_node": source_node,
        "source_pin": source_pin,
        "sink_node": sink_node,
        "sink_pin": sink_pin,
        "splice_node": splice_node,
        "splice_input_pin": splice_input_pin,
        "splice_output_pin": splice_output_pin,
    })


# ============================================================================
# BPDOCTOR INTEGRATION
# ============================================================================


@bionics_tool(
    name="ue5_bpdoctor_scan",
    category="ue5_bpdoctor",
    safety_tier=SafetyTier.SAFE,
    read_only=True,
    aliases=["bpdoctor-scan", "scan-blueprints"],
    title="BPDoctor Scan",
)
def ue5_bpdoctor_scan(
    asset_path: Annotated[str, "Specific BP to scan (omit for full project)"] = "",
    scan_path: Annotated[str, "Project path to scan"] = "/Game",
) -> ToolResult:
    """Run BPDoctor diagnostic on a Blueprint or full project. Returns health grade + issues."""
    return _call_tool("bpdoctor_scan", {
        "asset_path": asset_path,
        "scan_path": scan_path,
    })


@bionics_tool(
    name="ue5_bpdoctor_results",
    category="ue5_bpdoctor",
    safety_tier=SafetyTier.SAFE,
    read_only=True,
    aliases=["bpdoctor-results"],
    title="BPDoctor Results",
)
def ue5_bpdoctor_results(
    severity_filter: Annotated[str, "Filter: error, warning, info"] = "",
    check_code_filter: Annotated[str, "Filter by check code (e.g. NULL_ANIM_REF)"] = "",
    asset_path_filter: Annotated[str, "Filter by asset path substring"] = "",
    auto_fixable_only: Annotated[bool, "Only show auto-fixable issues"] = False,
) -> ToolResult:
    """Get filtered results from last BPDoctor scan."""
    return _call_tool("bpdoctor_results", {
        "severity_filter": severity_filter,
        "check_code_filter": check_code_filter,
        "asset_path_filter": asset_path_filter,
        "auto_fixable_only": auto_fixable_only,
    })


@bionics_tool(
    name="ue5_bpdoctor_fix",
    category="ue5_bpdoctor",
    safety_tier=SafetyTier.MODERATE,
    read_only=False,
    aliases=["bpdoctor-fix"],
    title="BPDoctor Fix",
)
def ue5_bpdoctor_fix(
    issue_index: Annotated[int, "Issue index from bpdoctor_scan results"],
) -> ToolResult:
    """Apply a specific BPDoctor auto-fix by issue index."""
    return _call_tool("bpdoctor_fix", {"issue_index": issue_index})


@bionics_tool(
    name="ue5_bpdoctor_fix_all",
    category="ue5_bpdoctor",
    safety_tier=SafetyTier.DESTRUCTIVE,
    destructive=True,
    read_only=False,
    aliases=["bpdoctor-fix-all"],
    title="BPDoctor Fix All",
)
def ue5_bpdoctor_fix_all(
    asset_path_filter: Annotated[str, "Only fix issues in BPs matching this path"] = "",
    rescan_after: Annotated[bool, "Re-scan after applying fixes to verify"] = True,
) -> ToolResult:
    """Apply ALL auto-fixable BPDoctor issues. Re-scans to verify."""
    return _call_tool("bpdoctor_fix_all", {
        "asset_path_filter": asset_path_filter,
        "rescan_after": rescan_after,
    })
