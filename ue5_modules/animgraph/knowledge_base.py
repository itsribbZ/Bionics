"""Bionics AnimGraph Knowledge Base — UE5 AnimGraph Expert Brain.

Complete reference for UE5 5.7 AnimGraph: node types, pin semantics,
context menus, navigation, compilation, Details panel, and editor rules.
Bionics uses this to understand what it's looking at and what actions
are valid at any point in the AnimGraph editor.

This replaces "guessing" with deterministic knowledge.
"""

from dataclasses import dataclass, field
from enum import Enum

# ============================================================
# 1. PIN TYPES — AnimGraph uses different pins than regular BP
# ============================================================

class PinType(Enum):
    """AnimGraph pin types with their visual identifiers."""
    POSE = "pose"          # White circle/arrow — carries animation pose data
    FLOAT = "float"        # Green circle — numeric value
    BOOL = "bool"          # Red circle — boolean
    INT = "int"            # Cyan/teal circle — integer
    ALPHA = "alpha"        # Green — blend alpha (0-1)
    BONE_REF = "bone_ref"  # Yellow — bone reference
    EXEC = "exec"          # White diamond — execution flow (rare in AnimGraph)
    ENUM = "enum"          # Cyan — enum type


@dataclass
class PinInfo:
    """Complete information about a pin."""
    name: str
    pin_type: PinType
    direction: str  # "input" or "output"
    display_name: str = ""
    tooltip: str = ""
    can_connect_to: list[str] = field(default_factory=list)

    @property
    def color_bgr(self) -> tuple[int, int, int]:
        """BGR color for OpenCV matching."""
        return PIN_COLORS.get(self.pin_type, (200, 200, 200))


# BGR colors for pin detection (measured from UE5 5.7 dark theme)
PIN_COLORS = {
    PinType.POSE:     (255, 255, 255),  # White
    PinType.FLOAT:    (0, 200, 0),      # Green
    PinType.BOOL:     (0, 0, 200),      # Red
    PinType.INT:      (200, 200, 0),    # Cyan
    PinType.ALPHA:    (0, 200, 0),      # Green (same as float)
    PinType.BONE_REF: (0, 200, 255),    # Yellow
    PinType.EXEC:     (255, 255, 255),  # White diamond
    PinType.ENUM:     (200, 200, 0),    # Cyan
}


# ============================================================
# 2. NODE TYPES — Every node Bionics needs to know
# ============================================================

class NodeCategory(Enum):
    """Categories of AnimGraph nodes."""
    STATE_MACHINE = "state_machine"
    BLEND = "blend"
    POSE = "pose"
    SLOT = "slot"
    CACHE = "cache"
    MODIFIER = "modifier"
    IK = "ik"
    CONTROL = "control"
    CONVERSION = "conversion"


@dataclass
class NodeType:
    """Complete definition of an AnimGraph node type."""
    class_name: str
    display_name: str
    search_name: str       # What to type in context menu search
    category: NodeCategory
    input_pins: list[PinInfo] = field(default_factory=list)
    output_pins: list[PinInfo] = field(default_factory=list)
    has_details: bool = True
    key_properties: list[str] = field(default_factory=list)
    notes: str = ""


# Master registry of AnimGraph node types
ANIMGRAPH_NODES: dict[str, NodeType] = {}


def _register_nodes():
    """Populate the node registry with all known AnimGraph nodes."""

    # --- Output Pose (always present, cannot be deleted) ---
    ANIMGRAPH_NODES["OutputPose"] = NodeType(
        class_name="AnimGraphNode_Root",
        display_name="Output Pose",
        search_name="",  # Cannot be added — always exists
        category=NodeCategory.POSE,
        input_pins=[PinInfo("Result", PinType.POSE, "input",
                            "Final Animation Pose",
                            "The final pose output to the skeleton")],
        output_pins=[],
        notes="Always exists. Cannot be deleted or duplicated. "
              "This is the rightmost node — everything feeds INTO it."
    )

    # --- State Machine ---
    ANIMGRAPH_NODES["StateMachine"] = NodeType(
        class_name="AnimGraphNode_StateMachine",
        display_name="State Machine",
        search_name="Add New State Machine",
        category=NodeCategory.STATE_MACHINE,
        input_pins=[],
        output_pins=[PinInfo("Pose", PinType.POSE, "output")],
        key_properties=["StateMachineName"],
        notes="Double-click to enter. Shows Entry node + states inside. "
              "Right-click inside to add states. Entry must connect to default state."
    )

    # --- Blend Space Player ---
    ANIMGRAPH_NODES["BlendSpacePlayer"] = NodeType(
        class_name="AnimGraphNode_BlendSpacePlayer",
        display_name="Blend Space Player",
        search_name="Blend Space Player",
        category=NodeCategory.BLEND,
        input_pins=[
            PinInfo("X", PinType.FLOAT, "input", "X Axis", "Horizontal blend axis (e.g. Direction)"),
            PinInfo("Y", PinType.FLOAT, "input", "Y Axis", "Vertical blend axis (e.g. Speed)"),
        ],
        output_pins=[PinInfo("Pose", PinType.POSE, "output")],
        key_properties=["BlendSpace"],
        notes="MUST set the BlendSpace asset in Details panel — it's not on the node itself. "
              "Click node, find 'Blend Space' dropdown in Details, search for your BS_ asset."
    )

    # --- Sequence Player ---
    ANIMGRAPH_NODES["SequencePlayer"] = NodeType(
        class_name="AnimGraphNode_SequencePlayer",
        display_name="Play Sequence",
        search_name="Play",
        category=NodeCategory.POSE,
        input_pins=[],
        output_pins=[PinInfo("Pose", PinType.POSE, "output")],
        key_properties=["Sequence"],
        notes="Set the AnimSequence asset in Details panel."
    )

    # --- Slot Node (for Montages) ---
    ANIMGRAPH_NODES["Slot"] = NodeType(
        class_name="AnimGraphNode_Slot",
        display_name="Slot 'DefaultSlot'",
        search_name="Slot",
        category=NodeCategory.SLOT,
        input_pins=[PinInfo("Source", PinType.POSE, "input",
                            "Source Pose", "Base pose (from SM or blend)")],
        output_pins=[PinInfo("Pose", PinType.POSE, "output",
                             "Output Pose", "Pose with montage layered on top")],
        key_properties=["SlotNodeName"],
        notes="CRITICAL: This is how montages play. Chain: SM → Slot → Output Pose. "
              "The slot name must match what PlayMontage uses (default: 'DefaultSlot'). "
              "Without this node, PlayMontage calls do NOTHING visible."
    )

    # --- Layered Blend Per Bone ---
    ANIMGRAPH_NODES["LayeredBlendPerBone"] = NodeType(
        class_name="AnimGraphNode_LayeredBoneBlend",
        display_name="Layered Blend Per Bone",
        search_name="Layered Blend Per Bone",
        category=NodeCategory.BLEND,
        input_pins=[
            PinInfo("BasePose", PinType.POSE, "input", "Base Pose"),
            PinInfo("BlendPose0", PinType.POSE, "input", "Blend Pose 0"),
            PinInfo("BlendWeight0", PinType.ALPHA, "input", "Blend Weight 0"),
        ],
        output_pins=[PinInfo("Pose", PinType.POSE, "output")],
        key_properties=["LayerSetup"],
        notes="T-POSE WARNING: If BlendWeight is 0, the blend layer shows the bind pose "
              "(T-pose). Always ensure weights are driven correctly. "
              "Layer setup defines which bones are affected by which layer."
    )

    # --- Blend Poses by Bool ---
    ANIMGRAPH_NODES["BlendPosesByBool"] = NodeType(
        class_name="AnimGraphNode_BlendListByBool",
        display_name="Blend Poses by bool",
        search_name="Blend Poses by bool",
        category=NodeCategory.BLEND,
        input_pins=[
            PinInfo("TruePose", PinType.POSE, "input", "True Pose"),
            PinInfo("FalsePose", PinType.POSE, "input", "False Pose"),
            PinInfo("bActiveTrueValue", PinType.BOOL, "input", "Active Value"),
        ],
        output_pins=[PinInfo("Pose", PinType.POSE, "output")],
        notes="Switches between two poses based on a boolean."
    )

    # --- Blend Poses by Int ---
    ANIMGRAPH_NODES["BlendPosesByInt"] = NodeType(
        class_name="AnimGraphNode_BlendListByInt",
        display_name="Blend Poses by int",
        search_name="Blend Poses by int",
        category=NodeCategory.BLEND,
        input_pins=[
            PinInfo("Pose0", PinType.POSE, "input"),
            PinInfo("ActiveIndex", PinType.INT, "input"),
        ],
        output_pins=[PinInfo("Pose", PinType.POSE, "output")],
        notes="Switches between N poses based on integer index. Add pins via right-click."
    )

    # --- Two Bone IK ---
    ANIMGRAPH_NODES["TwoBoneIK"] = NodeType(
        class_name="AnimGraphNode_TwoBoneIK",
        display_name="Two Bone IK",
        search_name="Two Bone IK",
        category=NodeCategory.IK,
        input_pins=[PinInfo("ComponentPose", PinType.POSE, "input")],
        output_pins=[PinInfo("Pose", PinType.POSE, "output")],
        key_properties=["IKBone", "EffectorTarget", "JointTarget"],
        notes="Used for foot IK, hand IK. Set IKBone in Details."
    )

    # --- FABRIK ---
    ANIMGRAPH_NODES["FABRIK"] = NodeType(
        class_name="AnimGraphNode_Fabrik",
        display_name="FABRIK",
        search_name="FABRIK",
        category=NodeCategory.IK,
        input_pins=[PinInfo("ComponentPose", PinType.POSE, "input")],
        output_pins=[PinInfo("Pose", PinType.POSE, "output")],
        key_properties=["TipBone", "RootBone", "EffectorTarget"],
    )

    # --- Save Cached Pose ---
    ANIMGRAPH_NODES["SaveCachedPose"] = NodeType(
        class_name="AnimGraphNode_SaveCachedPose",
        display_name="Save Cached Pose",
        search_name="New Save Cached Pose",
        category=NodeCategory.CACHE,
        input_pins=[PinInfo("Pose", PinType.POSE, "input")],
        output_pins=[],
        key_properties=["CachePoseName"],
        notes="Saves a pose snapshot that can be used multiple times via Use Cached Pose."
    )

    # --- Use Cached Pose ---
    ANIMGRAPH_NODES["UseCachedPose"] = NodeType(
        class_name="AnimGraphNode_UseCachedPose",
        display_name="Use Cached Pose",
        search_name="Use Cached Pose",
        category=NodeCategory.CACHE,
        input_pins=[],
        output_pins=[PinInfo("Pose", PinType.POSE, "output")],
        notes="References a SaveCachedPose node. Select which cache in Details."
    )

    # --- Apply Additive ---
    ANIMGRAPH_NODES["ApplyAdditive"] = NodeType(
        class_name="AnimGraphNode_ApplyAdditive",
        display_name="Apply Additive",
        search_name="Apply Additive",
        category=NodeCategory.BLEND,
        input_pins=[
            PinInfo("Base", PinType.POSE, "input"),
            PinInfo("Additive", PinType.POSE, "input"),
            PinInfo("Alpha", PinType.ALPHA, "input"),
        ],
        output_pins=[PinInfo("Pose", PinType.POSE, "output")],
    )

    # --- Modify Bone ---
    ANIMGRAPH_NODES["ModifyBone"] = NodeType(
        class_name="AnimGraphNode_ModifyBone",
        display_name="Modify Bone",
        search_name="Modify Bone",
        category=NodeCategory.MODIFIER,
        input_pins=[PinInfo("ComponentPose", PinType.POSE, "input")],
        output_pins=[PinInfo("Pose", PinType.POSE, "output")],
        key_properties=["BoneToModify", "TranslationMode", "RotationMode"],
    )

    # --- Aim Offset ---
    ANIMGRAPH_NODES["AimOffset"] = NodeType(
        class_name="AnimGraphNode_AimOffsetLookAt",
        display_name="Aim Offset",
        search_name="Aim Offset",
        category=NodeCategory.BLEND,
        input_pins=[
            PinInfo("BasePose", PinType.POSE, "input"),
            PinInfo("Yaw", PinType.FLOAT, "input"),
            PinInfo("Pitch", PinType.FLOAT, "input"),
        ],
        output_pins=[PinInfo("Pose", PinType.POSE, "output")],
        key_properties=["AimOffsetAsset"],
    )

    # --- Blend ---
    ANIMGRAPH_NODES["Blend"] = NodeType(
        class_name="AnimGraphNode_TwoWayBlend",
        display_name="Blend",
        search_name="Blend",
        category=NodeCategory.BLEND,
        input_pins=[
            PinInfo("A", PinType.POSE, "input"),
            PinInfo("B", PinType.POSE, "input"),
            PinInfo("Alpha", PinType.ALPHA, "input"),
        ],
        output_pins=[PinInfo("Pose", PinType.POSE, "output")],
    )

    # --- Inertialization ---
    ANIMGRAPH_NODES["Inertialization"] = NodeType(
        class_name="AnimGraphNode_Inertialization",
        display_name="Inertialization",
        search_name="Inertialization",
        category=NodeCategory.BLEND,
        input_pins=[PinInfo("Source", PinType.POSE, "input")],
        output_pins=[PinInfo("Pose", PinType.POSE, "output")],
        notes="UE5 preferred transition method. Replaces crossfades with physically-based blending. "
              "Set on state machine transitions for smoother results."
    )

    # --- Linked Anim Graph ---
    ANIMGRAPH_NODES["LinkedAnimGraph"] = NodeType(
        class_name="AnimGraphNode_LinkedAnimGraph",
        display_name="Linked Anim Graph",
        search_name="Linked Anim Graph",
        category=NodeCategory.CONTROL,
        input_pins=[],
        output_pins=[PinInfo("Pose", PinType.POSE, "output")],
        key_properties=["InstanceClass"],
        notes="References another AnimBP. Used for modular animation architecture."
    )


_register_nodes()


# ============================================================
# 3. EDITOR LAYOUT — Where things are on screen
# ============================================================

@dataclass
class EditorRegion:
    """A named region of the AnimGraph editor."""
    name: str
    description: str
    typical_x_range: tuple[int, int]  # Min/max X at 1080p
    typical_y_range: tuple[int, int]  # Min/max Y at 1080p


EDITOR_REGIONS = {
    "graph_canvas": EditorRegion(
        "Graph Canvas", "Main node editing area",
        (250, 1600), (80, 900)
    ),
    "details_panel": EditorRegion(
        "Details Panel", "Property inspector (right side)",
        (1600, 1920), (80, 1080)
    ),
    "my_blueprint": EditorRegion(
        "My Blueprint", "Variable/function list (left panel)",
        (0, 250), (80, 600)
    ),
    "toolbar": EditorRegion(
        "Toolbar", "Compile, Save, Browse buttons",
        (0, 1920), (0, 50)
    ),
    "breadcrumb": EditorRegion(
        "Breadcrumb Bar", "Navigation path (top of graph area)",
        (250, 1600), (50, 80)
    ),
    "anim_preview": EditorRegion(
        "Anim Preview", "3D viewport showing skeleton",
        (0, 400), (600, 1080)
    ),
}


# ============================================================
# 4. CONTEXT MENU — What appears when you right-click
# ============================================================

@dataclass
class ContextMenuItem:
    """An item in the AnimGraph context menu."""
    display_text: str
    search_text: str   # What to type to find it quickly
    submenu: str = ""  # If nested under a submenu
    creates_node: str = ""  # Key in ANIMGRAPH_NODES if this creates a node


CONTEXT_MENU_ITEMS = [
    # Top-level items (no search needed)
    ContextMenuItem("Add New State Machine...", "State Machine", creates_node="StateMachine"),
    ContextMenuItem("Paste Here", ""),

    # Searchable items
    ContextMenuItem("Blend Space Player", "Blend Space Player", "Blends", "BlendSpacePlayer"),
    ContextMenuItem("Play Sequence", "Play", "Poses", "SequencePlayer"),
    ContextMenuItem("Slot 'DefaultSlot'", "Slot", "Montage", "Slot"),
    ContextMenuItem("Layered Blend Per Bone", "Layered Blend Per Bone", "Blends", "LayeredBlendPerBone"),
    ContextMenuItem("Blend Poses by bool", "Blend Poses by bool", "Blends", "BlendPosesByBool"),
    ContextMenuItem("Blend Poses by int", "Blend Poses by int", "Blends", "BlendPosesByInt"),
    ContextMenuItem("Two Bone IK", "Two Bone IK", "IK", "TwoBoneIK"),
    ContextMenuItem("FABRIK", "FABRIK", "IK", "FABRIK"),
    ContextMenuItem("New Save Cached Pose...", "Save Cached Pose", "Cached Poses", "SaveCachedPose"),
    ContextMenuItem("Use Cached Pose", "Use Cached Pose", "Cached Poses", "UseCachedPose"),
    ContextMenuItem("Apply Additive", "Apply Additive", "Blends", "ApplyAdditive"),
    ContextMenuItem("Modify Bone", "Modify Bone", "Skeletal Control", "ModifyBone"),
    ContextMenuItem("Aim Offset", "Aim Offset", "Blends", "AimOffset"),
    ContextMenuItem("Blend", "Blend", "Blends", "Blend"),
    ContextMenuItem("Inertialization", "Inertialization", "Blends", "Inertialization"),
    ContextMenuItem("Linked Anim Graph", "Linked Anim Graph", "Misc", "LinkedAnimGraph"),
]


# ============================================================
# 5. EDITOR RULES — The hard rules Bionics must follow
# ============================================================

EDITOR_RULES = {
    "pin_connection": [
        "AnimGraph nodes use POSE pins (white), not exec/data pins like EventGraph",
        "Pose pins are fat white circles with arrows, NOT thin white diamonds",
        "You can ONLY connect output pose pins to input pose pins of matching type",
        "Drag FROM an output pin TO an input pin — direction matters",
        "If you drag from a pin and release over empty space, a context menu appears with compatible nodes",
        "Float pins (green) connect to float inputs like BlendSpace X/Y axes",
        "Alpha pins are float pins in the 0-1 range — they appear green too",
    ],

    "context_menu": [
        "Right-click on empty graph canvas to open the context menu",
        "The context menu has a SEARCH BAR at the top — type to filter",
        "Context Sensitive checkbox: OFF shows ALL nodes, ON shows only compatible ones",
        "For AnimGraph, keep Context Sensitive OFF to see the full node list",
        "Right-click on a pin for pin-specific context menu (only shows nodes with matching pins)",
        "The search box is auto-focused — just start typing immediately after right-click",
        "Press Enter or click to select the first matching result",
    ],

    "state_machine": [
        "Double-click a State Machine node to enter it (navigate inside)",
        "Inside, you see an ENTRY node (green arrow) and state nodes",
        "The Entry node must be connected to exactly one default state",
        "Right-click inside a State Machine to add states (Add State)",
        "Each state has its own graph — double-click a state to enter it",
        "Drag from one state's edge to another to create a transition",
        "Transitions have rules (double-click the transition arrow to edit)",
        "Use Inertialization for transitions instead of crossfades in UE5",
    ],

    "navigation": [
        "Breadcrumb bar at top shows your location: AnimGraph > StateMachine > State",
        "Click any breadcrumb level to navigate back up",
        "Double-click a node to enter it (if it's a container like StateMachine or State)",
        "Home key fits all visible nodes in the viewport",
        "Ctrl+Home fits the entire graph",
        "Mouse wheel to zoom in/out",
        "Middle mouse button + drag to pan the graph",
        "Right mouse button + drag also pans (in graph area only)",
    ],

    "compilation": [
        "F7 compiles the AnimBP (same as clicking the Compile button)",
        "Green checkmark in toolbar = compiled successfully",
        "Red X in toolbar = compilation errors",
        "Yellow warning triangle = compiled with warnings",
        "You MUST compile after making graph changes for them to take effect",
        "Ctrl+S saves the asset (does NOT auto-compile)",
        "Compile THEN save: F7 → wait for checkmark → Ctrl+S",
    ],

    "details_panel": [
        "Click ANY node to see its properties in the Details panel (right side)",
        "Blend Space Player: set the BlendSpace asset via dropdown in Details",
        "Sequence Player: set the AnimSequence via dropdown in Details",
        "Slot node: set the SlotNodeName (default: 'DefaultSlot')",
        "State Machine: set the name in Details after selecting it",
        "Properties are NOT on the node itself — they're in the Details panel",
        "Search bar at top of Details panel filters properties by name",
    ],

    "common_mistakes": [
        "NEVER create multiple Output Pose nodes — there can only be one",
        "NEVER leave pose pins unconnected — disconnected chains cause T-pose",
        "The Slot node is REQUIRED for montages — without it, PlayMontage does nothing",
        "Blend weights of 0.0 on LayeredBlendPerBone shows bind pose (T-pose)",
        "Forgetting to set the asset on BlendSpacePlayer makes it output bind pose",
        "State Machine without Entry → State connection = no animation at all",
        "Not compiling after changes = changes don't take effect in PIE",
    ],
}


# ============================================================
# 6. COMMON WORKFLOWS — Step-by-step verified procedures
# ============================================================

@dataclass
class WorkflowStep:
    """A single step in a workflow."""
    action: str          # What to do
    target: str          # Where to do it
    verification: str    # How to know it worked
    notes: str = ""      # Extra info


@dataclass
class Workflow:
    """A complete, verified workflow."""
    name: str
    description: str
    prerequisite: str    # What must be true before starting
    steps: list[WorkflowStep]
    result: str          # What the outcome should be


WORKFLOWS: dict[str, Workflow] = {
    "basic_locomotion": Workflow(
        name="Wire Basic Locomotion",
        description="Create SM → Slot → Output Pose chain for basic movement + montages",
        prerequisite="AnimBP is open in AnimGraph view with only Output Pose visible",
        steps=[
            WorkflowStep(
                "Right-click on empty canvas left of Output Pose",
                "graph_canvas (around x=800, y=400)",
                "Context menu appears with search bar",
            ),
            WorkflowStep(
                "Type 'State Machine' in search bar, press Enter",
                "Context menu search bar",
                "New State Machine node appears on canvas",
                "Will prompt for a name — type 'Locomotion'"
            ),
            WorkflowStep(
                "Right-click between State Machine and Output Pose",
                "graph_canvas (around x=1000, y=400)",
                "Context menu appears",
            ),
            WorkflowStep(
                "Type 'Slot' in search bar, press Enter",
                "Context menu search bar",
                "Slot 'DefaultSlot' node appears",
            ),
            WorkflowStep(
                "Drag from StateMachine output pin to Slot 'Source' input pin",
                "StateMachine → Slot",
                "White wire appears connecting them",
            ),
            WorkflowStep(
                "Drag from Slot output pin to Output Pose 'Result' input pin",
                "Slot → Output Pose",
                "White wire appears connecting them, completing the chain",
            ),
            WorkflowStep(
                "Press F7 to compile",
                "Keyboard",
                "Green checkmark appears in toolbar",
            ),
            WorkflowStep(
                "Press Ctrl+S to save",
                "Keyboard",
                "Asset saved notification or title bar asterisk disappears",
            ),
        ],
        result="AnimBP has: StateMachine → Slot(DefaultSlot) → Output Pose. "
               "Montages will work via DefaultSlot. Locomotion goes through SM."
    ),

    "add_blend_space_state": Workflow(
        name="Add BlendSpace State to State Machine",
        description="Add a locomotion state using a BlendSpace inside a State Machine",
        prerequisite="Inside a State Machine (double-clicked into it), Entry node visible",
        steps=[
            WorkflowStep(
                "Right-click on empty canvas to the right of Entry",
                "State Machine canvas",
                "Context menu appears",
            ),
            WorkflowStep(
                "Select 'Add State'",
                "Context menu",
                "New state node appears, prompts for name",
                "Name it 'Locomotion' or appropriate name"
            ),
            WorkflowStep(
                "Drag from Entry arrow to the new state",
                "Entry → Locomotion",
                "Transition arrow appears (Entry always goes to default state)",
            ),
            WorkflowStep(
                "Double-click the Locomotion state to enter it",
                "Locomotion state node",
                "Breadcrumb updates to: AnimGraph > SM > Locomotion",
            ),
            WorkflowStep(
                "Right-click in the state's canvas",
                "State canvas (empty area)",
                "Context menu appears",
            ),
            WorkflowStep(
                "Type 'Blend Space Player', press Enter",
                "Context menu search",
                "BlendSpace Player node appears",
            ),
            WorkflowStep(
                "Click the BlendSpace Player node to select it",
                "BlendSpace Player node",
                "Details panel shows its properties",
            ),
            WorkflowStep(
                "In Details panel, find 'Blend Space' dropdown, click it",
                "Details panel",
                "Asset picker/dropdown appears",
            ),
            WorkflowStep(
                "Search for and select your blend space asset (e.g. BS_Trooper_WalkRun)",
                "Asset picker",
                "BlendSpace asset assigned — node updates to show the name",
            ),
            WorkflowStep(
                "Connect BlendSpace Player output to Output Animation Pose",
                "BlendSpace Player → Output Pose (inside state)",
                "Wire connects them",
            ),
            WorkflowStep(
                "Click breadcrumb to navigate back to State Machine level",
                "Breadcrumb bar",
                "Returns to SM view with all states visible",
            ),
        ],
        result="State Machine has an Entry → Locomotion state with BlendSpace Player inside."
    ),
}


# ============================================================
# 7. QUERY INTERFACE — How Bionics asks questions
# ============================================================

class AnimGraphKB:
    """Query interface to the AnimGraph Knowledge Base."""

    @staticmethod
    def get_node(name: str) -> NodeType | None:
        """Look up a node type by name (case-insensitive, partial match)."""
        name_lower = name.lower()
        for key, node in ANIMGRAPH_NODES.items():
            if (name_lower in key.lower() or
                name_lower in node.display_name.lower() or
                name_lower in node.class_name.lower()):
                return node
        return None

    @staticmethod
    def get_nodes_by_category(category: NodeCategory) -> list[NodeType]:
        """Get all nodes in a category."""
        return [n for n in ANIMGRAPH_NODES.values() if n.category == category]

    @staticmethod
    def get_node_for_task(task: str) -> list[NodeType]:
        """Suggest nodes for a given task description."""
        task_lower = task.lower()
        results = []

        keyword_map = {
            "locomotion": ["StateMachine", "BlendSpacePlayer"],
            "walk": ["BlendSpacePlayer"],
            "run": ["BlendSpacePlayer"],
            "blend": ["BlendSpacePlayer", "Blend", "BlendPosesByBool", "BlendPosesByInt"],
            "montage": ["Slot"],
            "attack": ["Slot"],
            "combat": ["Slot", "LayeredBlendPerBone"],
            "ik": ["TwoBoneIK", "FABRIK"],
            "foot": ["TwoBoneIK"],
            "hand": ["TwoBoneIK"],
            "aim": ["AimOffset"],
            "cache": ["SaveCachedPose", "UseCachedPose"],
            "transition": ["Inertialization"],
            "layer": ["LayeredBlendPerBone"],
            "additive": ["ApplyAdditive"],
            "upper body": ["LayeredBlendPerBone"],
        }

        for keyword, node_names in keyword_map.items():
            if keyword in task_lower:
                for nn in node_names:
                    node = ANIMGRAPH_NODES.get(nn)
                    if node and node not in results:
                        results.append(node)

        return results

    @staticmethod
    def get_rules(category: str) -> list[str]:
        """Get editor rules for a category."""
        return EDITOR_RULES.get(category, [])

    @staticmethod
    def get_all_rules() -> dict[str, list[str]]:
        """Get all editor rules."""
        return EDITOR_RULES

    @staticmethod
    def get_workflow(name: str) -> Workflow | None:
        """Get a named workflow."""
        return WORKFLOWS.get(name)

    @staticmethod
    def get_all_workflows() -> dict[str, Workflow]:
        """Get all workflows."""
        return WORKFLOWS

    @staticmethod
    def search_context_menu(search_text: str) -> list[ContextMenuItem]:
        """Find context menu items matching search text."""
        search_lower = search_text.lower()
        return [
            item for item in CONTEXT_MENU_ITEMS
            if search_lower in item.display_text.lower()
            or search_lower in item.search_text.lower()
        ]

    @staticmethod
    def get_pin_type(pin_name: str) -> PinType | None:
        """Determine pin type from a pin name."""
        pin_lower = pin_name.lower()
        if "pose" in pin_lower or "result" in pin_lower or "source" in pin_lower:
            return PinType.POSE
        if "alpha" in pin_lower or "weight" in pin_lower:
            return PinType.ALPHA
        if pin_lower in ("x", "y", "speed", "direction"):
            return PinType.FLOAT
        if pin_lower.startswith("b") and pin_lower[1:2].isupper():
            return PinType.BOOL
        return None

    @staticmethod
    def validate_connection(
        source_node: str, source_pin: str,
        target_node: str, target_pin: str
    ) -> tuple[bool, str]:
        """Validate whether two pins can be connected."""
        src = ANIMGRAPH_NODES.get(source_node)
        tgt = ANIMGRAPH_NODES.get(target_node)

        if not src:
            return False, f"Unknown source node: {source_node}"
        if not tgt:
            return False, f"Unknown target node: {target_node}"

        # Find output pin on source
        src_pin = None
        for p in src.output_pins:
            if p.name.lower() == source_pin.lower():
                src_pin = p
                break
        if not src_pin:
            return False, f"No output pin '{source_pin}' on {source_node}"

        # Find input pin on target
        tgt_pin = None
        for p in tgt.input_pins:
            if p.name.lower() == target_pin.lower():
                tgt_pin = p
                break
        if not tgt_pin:
            return False, f"No input pin '{target_pin}' on {target_node}"

        # Check type compatibility
        if src_pin.pin_type == tgt_pin.pin_type:
            return True, "Compatible"
        if {src_pin.pin_type, tgt_pin.pin_type} == {PinType.FLOAT, PinType.ALPHA}:
            return True, "Float/Alpha are interchangeable"

        return False, (f"Type mismatch: {source_node}.{source_pin} is {src_pin.pin_type.value}, "
                       f"{target_node}.{target_pin} is {tgt_pin.pin_type.value}")

    @staticmethod
    def get_standard_chain() -> str:
        """Return the standard AnimGraph chain description."""
        return (
            "Standard AnimGraph chain (left to right):\n"
            "  [State Machine] → [Slot 'DefaultSlot'] → [Output Pose]\n"
            "  \n"
            "  - State Machine: contains locomotion states (idle, walk, run, jump)\n"
            "  - Slot: enables montages (combat, emotes) to layer on top\n"
            "  - Output Pose: final output to the skeleton (always rightmost)\n"
            "  \n"
            "  All pose pins are white. All connections flow left→right.\n"
            "  F7 to compile, Ctrl+S to save."
        )
