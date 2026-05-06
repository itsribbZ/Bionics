"""UE5 EventGraph (K2 / Ubergraph) Tools — programmatic Blueprint EventGraph editing.

Companion to `ue5_animgraph.py`. Where AnimGraph tools wire animation pose flow,
EventGraph tools wire EVENT-DRIVEN logic — PlayMontage calls, hitstop timers,
GameplayCue triggers, CameraShake spawns, AnimNotify event handlers, member-variable
reads/writes. This is the surface that closes the "0% combat polish automatable" gap
flagged in Bionics v0.5.10's audit.

These tools call the C++ BionicsBridge plugin's EventGraph manipulation endpoints
(see `plugins/BionicsBridge/Source/BionicsBridgeEditor/Private/Tools/EventGraphTools.cpp`).
UE5 Python's graph-editing API is incomplete; native C++ is the only reliable path.

5 tools:
    - ue5_query_eventgraph        — read every node + pins + connections
    - ue5_eventgraph_add_call_function — add UFUNCTION call (PlayMontage, hitstop, GameplayCue, etc.)
    - ue5_eventgraph_add_variable_node — read/write member variables
    - ue5_eventgraph_add_event    — add BeginPlay/Tick/custom event entry
    - ue5_wire_eventgraph_pins    — connect pins (exec + data)

VERIFICATION REQUIRED: shipped without UE5 rebuild + live-fire smoke. See
`scripts/smoke_test_eventgraph.ps1` (to be added) for the verification recipe.
"""

from __future__ import annotations

from typing import Annotated

from bionics_tools.ue5_native import call_bridge_tool as _call_tool
from core.bridge import SafetyTier, ToolResult, bionics_tool

# ============================================================================
# QUERY
# ============================================================================


@bionics_tool(
    name="ue5_query_eventgraph",
    category="ue5_eventgraph",
    safety_tier=SafetyTier.SAFE,
    read_only=True,
    aliases=["query-eventgraph", "get-eventgraph"],
    title="Query EventGraph",
)
def ue5_query_eventgraph(
    asset_path: Annotated[str, "Blueprint path like /Game/Blueprints/BP_Character"],
    include_hidden_pins: Annotated[bool, "Include hidden pins in output"] = False,
) -> ToolResult:
    """Get full EventGraph (Ubergraph) structure: every node + pins + connections + node kind tag."""
    return _call_tool("query_eventgraph", {
        "asset_path": asset_path,
        "include_hidden_pins": include_hidden_pins,
    })


# ============================================================================
# CREATE — Call Function (the high-leverage tool: PlayMontage, hitstop, GameplayCue, etc.)
# ============================================================================


@bionics_tool(
    name="ue5_eventgraph_add_call_function",
    category="ue5_eventgraph",
    safety_tier=SafetyTier.MODERATE,
    read_only=False,
    aliases=["add-eventgraph-call-function", "add-call-function-node"],
    title="Add EventGraph Call Function Node",
)
def ue5_eventgraph_add_call_function(
    asset_path: Annotated[str, "Blueprint path like /Game/Blueprints/BP_Character"],
    function_name: Annotated[str, "UFUNCTION name (e.g. 'PlayMontage', 'SpawnEmitterAtLocation', "
                                  "'SetTimerByFunctionName', 'ExecuteGameplayCue')"],
    target_class: Annotated[str, "Target class short name. 'GameplayStatics' for static helpers, "
                                  "'KismetMathLibrary' for math, 'KismetSystemLibrary' for utilities, "
                                  "'AbilitySystemComponent' for GAS. Leave empty for member function "
                                  "on the Blueprint's parent class."] = "",
    pos_x: Annotated[int, "X position in graph"] = 0,
    pos_y: Annotated[int, "Y position in graph"] = 0,
) -> ToolResult:
    """Add a CallFunction node to the EventGraph. Highest-leverage EventGraph tool —
    unlocks PlayMontage, GameplayCue triggers, hitstop timers, CameraShake spawns,
    and any other UFUNCTION wiring."""
    return _call_tool("add_eventgraph_call_function", {
        "asset_path": asset_path,
        "function_name": function_name,
        "target_class": target_class,
        "pos_x": pos_x,
        "pos_y": pos_y,
    })


# ============================================================================
# CREATE — Variable Get / Set
# ============================================================================


@bionics_tool(
    name="ue5_eventgraph_add_variable_node",
    category="ue5_eventgraph",
    safety_tier=SafetyTier.MODERATE,
    read_only=False,
    aliases=["add-eventgraph-variable-node", "add-var-node"],
    title="Add EventGraph Variable Get/Set Node",
)
def ue5_eventgraph_add_variable_node(
    asset_path: Annotated[str, "Blueprint path like /Game/Blueprints/BP_Character"],
    variable_name: Annotated[str, "Variable name (must exist on the BP class — query first if unsure)"],
    operation: Annotated[str, "'get' to read, 'set' to write"],
    pos_x: Annotated[int, "X position in graph"] = 0,
    pos_y: Annotated[int, "Y position in graph"] = 0,
) -> ToolResult:
    """Add a VariableGet or VariableSet node to the EventGraph. Reads/writes a member variable
    on the Blueprint. Combat polish path: read Health, write LastHitTime, set bIsInvulnerable."""
    return _call_tool("add_eventgraph_variable_node", {
        "asset_path": asset_path,
        "variable_name": variable_name,
        "operation": operation,
        "pos_x": pos_x,
        "pos_y": pos_y,
    })


# ============================================================================
# CREATE — Event entry (BeginPlay / Tick / Custom)
# ============================================================================


@bionics_tool(
    name="ue5_eventgraph_add_event",
    category="ue5_eventgraph",
    safety_tier=SafetyTier.MODERATE,
    read_only=False,
    aliases=["add-eventgraph-event"],
    title="Add EventGraph Event Node",
)
def ue5_eventgraph_add_event(
    asset_path: Annotated[str, "Blueprint path like /Game/Blueprints/BP_Character"],
    event_type: Annotated[str, "'engine' for stock events (BeginPlay/Tick/etc.), "
                                "'custom' for user-defined events"],
    event_name: Annotated[str, "Engine: 'ReceiveBeginPlay', 'ReceiveEndPlay', 'ReceiveTick', "
                                "'ReceiveActorBeginOverlap', 'ReceiveAnyDamage'. "
                                "Custom: any name (e.g. 'OnHitStop', 'OnMontageNotify')"],
    pos_x: Annotated[int, "X position in graph"] = 0,
    pos_y: Annotated[int, "Y position in graph"] = 0,
) -> ToolResult:
    """Add an event entry node (engine event override OR custom event) to the EventGraph."""
    return _call_tool("add_eventgraph_event", {
        "asset_path": asset_path,
        "event_type": event_type,
        "event_name": event_name,
        "pos_x": pos_x,
        "pos_y": pos_y,
    })


# ============================================================================
# WIRE
# ============================================================================


@bionics_tool(
    name="ue5_wire_eventgraph_pins",
    category="ue5_eventgraph",
    safety_tier=SafetyTier.MODERATE,
    read_only=False,
    aliases=["wire-eventgraph-pins", "connect-eventgraph-pins"],
    title="Wire EventGraph Pins",
)
def ue5_wire_eventgraph_pins(
    asset_path: Annotated[str, "Blueprint path like /Game/Blueprints/BP_Character"],
    source_node: Annotated[str, "Source node name (use ue5_query_eventgraph to discover names)"],
    source_pin: Annotated[str, "Output pin name on source. Exec output is typically 'then'; "
                                "data outputs use the parameter name from the UFunction."],
    target_node: Annotated[str, "Target node name"],
    target_pin: Annotated[str, "Input pin name on target. Exec input is typically 'execute'; "
                                "data inputs use the parameter name."],
    auto_compile: Annotated[bool, "Compile the BP after wiring"] = True,
) -> ToolResult:
    """Wire two pins in the EventGraph using UEdGraphSchema_K2 (type-safe, handles wildcards
    and automatic conversion nodes). Auto-compiles after wiring."""
    return _call_tool("wire_eventgraph_pins", {
        "asset_path": asset_path,
        "source_node": source_node,
        "source_pin": source_pin,
        "target_node": target_node,
        "target_pin": target_pin,
        "auto_compile": auto_compile,
    })
