"""Bionics AnimGraph Element Templates — Vision Detection Configs.

Defines how Bionics identifies AnimGraph UI elements on screen:
- Color signatures (HSV ranges for pin types, node headers, buttons)
- Structural patterns (node shape, pin layout, text regions)
- Template image specifications (what to capture for OpenCV matching)
- Spatial relationships (where elements appear relative to each other)

These configs drive the ElementDetector (core/precision.py) for
AnimGraph-specific operations when the API path isn't available.
"""

from dataclasses import dataclass, field
from pathlib import Path

TEMPLATE_DIR = Path(__file__).parent / "templates"


# ============================================================
# 1. COLOR SIGNATURES — HSV ranges for element detection
# ============================================================

@dataclass
class ColorSignature:
    """HSV color range for detecting a UI element type."""
    name: str
    hsv_lower: tuple[int, int, int]  # H, S, V (OpenCV: H 0-179, S 0-255, V 0-255)
    hsv_upper: tuple[int, int, int]
    min_area: int = 20               # Minimum contour area to consider
    description: str = ""


# UE5 5.7 dark theme — measured from actual editor screenshots
COLOR_SIGNATURES = {
    # --- Pin colors ---
    "pin_pose": ColorSignature(
        "Pose Pin (White)", (0, 0, 200), (179, 30, 255),
        min_area=30,
        description="White circle — carries pose data. Largest pin type."
    ),
    "pin_float": ColorSignature(
        "Float Pin (Green)", (35, 100, 100), (85, 255, 255),
        min_area=20,
        description="Green circle — numeric value (speed, direction, alpha)"
    ),
    "pin_bool": ColorSignature(
        "Bool Pin (Red)", (0, 100, 100), (10, 255, 255),
        min_area=20,
        description="Red circle — boolean flag"
    ),
    "pin_int": ColorSignature(
        "Int Pin (Cyan)", (80, 100, 100), (100, 255, 255),
        min_area=20,
        description="Cyan/teal circle — integer value"
    ),

    # --- Node header colors ---
    "node_header_sm": ColorSignature(
        "State Machine Header", (0, 0, 40), (179, 30, 80),
        min_area=500,
        description="Dark gray header of State Machine node"
    ),
    "node_header_blend": ColorSignature(
        "Blend Node Header", (100, 30, 40), (130, 100, 100),
        min_area=500,
        description="Slightly blue-gray header of blend nodes"
    ),

    # --- UI chrome ---
    "compile_success": ColorSignature(
        "Compile Success (Green Check)", (35, 150, 150), (85, 255, 255),
        min_area=50,
        description="Green checkmark icon in toolbar"
    ),
    "compile_error": ColorSignature(
        "Compile Error (Red X)", (0, 150, 150), (10, 255, 255),
        min_area=50,
        description="Red X icon in toolbar — compilation failed"
    ),
    "entry_node": ColorSignature(
        "Entry Node (Green Arrow)", (35, 150, 100), (85, 255, 255),
        min_area=100,
        description="Green arrow marking the entry point in a state machine"
    ),
    "transition_arrow": ColorSignature(
        "Transition Arrow (White)", (0, 0, 180), (179, 40, 255),
        min_area=40,
        description="White directional arrow between states"
    ),
    "wire_connected": ColorSignature(
        "Connected Wire (White)", (0, 0, 200), (179, 20, 255),
        min_area=10,
        description="White wire connecting two pose pins"
    ),

    # --- Context menu ---
    "context_menu_bg": ColorSignature(
        "Context Menu Background", (0, 0, 20), (179, 30, 50),
        min_area=2000,
        description="Dark popup background of context menu"
    ),
    "search_box": ColorSignature(
        "Search Box", (0, 0, 30), (179, 20, 70),
        min_area=500,
        description="Darker search input field at top of context menu"
    ),
}


# ============================================================
# 2. TEMPLATE IMAGE SPECIFICATIONS
# ============================================================

@dataclass
class TemplateSpec:
    """Specification for a template image to capture or match."""
    name: str
    filename: str
    description: str
    expected_size: tuple[int, int]  # Width x Height at 1080p
    color_hint: str = ""           # Which ColorSignature to use as pre-filter
    anchor_region: str = ""        # Which EditorRegion this appears in
    multi_scale: bool = True       # Whether to search at multiple scales
    scales: list[float] = field(default_factory=lambda: [0.8, 0.9, 1.0, 1.1, 1.2])
    threshold: float = 0.75        # Match confidence threshold


# Templates that need to be captured from UE5
TEMPLATE_SPECS: dict[str, TemplateSpec] = {
    # --- Core nodes ---
    "output_pose_node": TemplateSpec(
        "Output Pose Node", "output_pose_node.png",
        "The Output Pose node (always rightmost, has 'Result' input pin)",
        expected_size=(150, 60),
        anchor_region="graph_canvas",
    ),
    "state_machine_node": TemplateSpec(
        "State Machine Node", "state_machine_node.png",
        "A State Machine node in the AnimGraph (compact dark rectangle)",
        expected_size=(180, 60),
        anchor_region="graph_canvas",
    ),
    "slot_node": TemplateSpec(
        "Slot Node", "slot_node.png",
        "Slot 'DefaultSlot' node (pass-through for montages)",
        expected_size=(200, 60),
        anchor_region="graph_canvas",
    ),
    "blend_space_player_node": TemplateSpec(
        "BlendSpace Player Node", "blend_space_player_node.png",
        "BlendSpace Player node with X/Y inputs",
        expected_size=(200, 80),
        anchor_region="graph_canvas",
    ),
    "entry_node": TemplateSpec(
        "Entry Node", "entry_node.png",
        "Green entry arrow inside a State Machine",
        expected_size=(80, 40),
        color_hint="entry_node",
        anchor_region="graph_canvas",
    ),

    # --- Pins ---
    "pin_pose_output": TemplateSpec(
        "Pose Output Pin", "pin_pose_output.png",
        "White circle output pin (right side of node)",
        expected_size=(16, 16),
        color_hint="pin_pose",
        multi_scale=False,
        threshold=0.7,
    ),
    "pin_pose_input": TemplateSpec(
        "Pose Input Pin", "pin_pose_input.png",
        "White circle input pin (left side of node)",
        expected_size=(16, 16),
        color_hint="pin_pose",
        multi_scale=False,
        threshold=0.7,
    ),
    "pin_float_input": TemplateSpec(
        "Float Input Pin", "pin_float_input.png",
        "Green circle input pin (for X, Y, Speed values)",
        expected_size=(12, 12),
        color_hint="pin_float",
        multi_scale=False,
        threshold=0.7,
    ),

    # --- Toolbar buttons ---
    "compile_button_ready": TemplateSpec(
        "Compile Button (Ready)", "compile_button_ready.png",
        "Compile button in toolbar — ready state (needs compile)",
        expected_size=(24, 24),
        anchor_region="toolbar",
        multi_scale=False,
    ),
    "compile_button_success": TemplateSpec(
        "Compile Button (Success)", "compile_button_success.png",
        "Green checkmark in toolbar — compiled successfully",
        expected_size=(24, 24),
        color_hint="compile_success",
        anchor_region="toolbar",
        multi_scale=False,
    ),
    "compile_button_error": TemplateSpec(
        "Compile Button (Error)", "compile_button_error.png",
        "Red X in toolbar — compilation failed",
        expected_size=(24, 24),
        color_hint="compile_error",
        anchor_region="toolbar",
        multi_scale=False,
    ),

    # --- Context menu ---
    "context_menu_search": TemplateSpec(
        "Context Menu Search Box", "context_menu_search.png",
        "The search text input at top of context menu",
        expected_size=(300, 25),
        anchor_region="graph_canvas",
        multi_scale=False,
    ),

    # --- Breadcrumb ---
    "breadcrumb_animgraph": TemplateSpec(
        "Breadcrumb AnimGraph", "breadcrumb_animgraph.png",
        "The 'AnimGraph' text in breadcrumb navigation bar",
        expected_size=(100, 20),
        anchor_region="breadcrumb",
        multi_scale=False,
    ),

    # --- State elements ---
    "state_node": TemplateSpec(
        "State Node", "state_node.png",
        "A named state inside a State Machine (rounded rectangle)",
        expected_size=(120, 40),
        anchor_region="graph_canvas",
    ),
    "transition_rule": TemplateSpec(
        "Transition Rule Icon", "transition_rule.png",
        "Small circle on a transition arrow (double-click to edit rule)",
        expected_size=(20, 20),
        anchor_region="graph_canvas",
        threshold=0.65,
    ),
}


# ============================================================
# 3. SPATIAL RELATIONSHIPS
# ============================================================

@dataclass
class SpatialRelation:
    """How two elements relate spatially."""
    reference: str       # Template name of reference element
    target: str          # Template name of target element
    direction: str       # "right_of", "left_of", "above", "below"
    distance_range: tuple[int, int]  # Min/max pixel distance at 1080p
    description: str = ""


SPATIAL_RELATIONS = [
    SpatialRelation(
        "output_pose_node", "slot_node", "left_of", (100, 400),
        "Slot node is always to the left of Output Pose"
    ),
    SpatialRelation(
        "slot_node", "state_machine_node", "left_of", (100, 400),
        "State Machine is always to the left of Slot"
    ),
    SpatialRelation(
        "pin_pose_output", "pin_pose_input", "left_of", (50, 500),
        "Output pins connect to input pins — output is on right side of source node"
    ),
    SpatialRelation(
        "entry_node", "state_node", "left_of", (50, 300),
        "Entry is always to the left, pointing to the default state"
    ),
    SpatialRelation(
        "compile_button_ready", "compile_button_success", "same_position", (0, 5),
        "Compile button changes appearance in-place (same position)"
    ),
]


# ============================================================
# 4. SCREENSHOT STATES — What to capture
# ============================================================

@dataclass
class ScreenshotState:
    """A specific editor state that should be captured as reference."""
    name: str
    description: str
    setup_steps: list[str]  # How to get the editor into this state
    elements_visible: list[str]  # Which templates should be visible
    filename: str


SCREENSHOT_STATES = [
    ScreenshotState(
        "animgraph_empty",
        "AnimGraph with only Output Pose (clean slate)",
        ["Open an AnimBP", "Navigate to AnimGraph tab",
         "Delete all nodes except Output Pose"],
        ["output_pose_node", "breadcrumb_animgraph"],
        "state_animgraph_empty.png"
    ),
    ScreenshotState(
        "animgraph_standard_chain",
        "Standard chain: StateMachine → Slot → Output Pose",
        ["Build the standard chain in AnimGraph"],
        ["output_pose_node", "slot_node", "state_machine_node"],
        "state_animgraph_standard.png"
    ),
    ScreenshotState(
        "context_menu_open",
        "Right-click context menu visible in AnimGraph",
        ["Right-click on empty area of AnimGraph canvas"],
        ["context_menu_search"],
        "state_context_menu.png"
    ),
    ScreenshotState(
        "context_menu_search",
        "Context menu with search text entered",
        ["Right-click in canvas", "Type 'Blend' in search box"],
        ["context_menu_search"],
        "state_context_menu_search.png"
    ),
    ScreenshotState(
        "state_machine_inside",
        "Inside a State Machine (Entry + states visible)",
        ["Double-click a State Machine node to enter it"],
        ["entry_node", "state_node", "breadcrumb_animgraph"],
        "state_sm_inside.png"
    ),
    ScreenshotState(
        "blend_space_selected",
        "BlendSpace Player node selected, Details panel showing properties",
        ["Click a BlendSpace Player node in the AnimGraph"],
        ["blend_space_player_node"],
        "state_bs_selected.png"
    ),
    ScreenshotState(
        "compile_success",
        "After successful compilation — green checkmark visible",
        ["Press F7 to compile a valid AnimBP"],
        ["compile_button_success"],
        "state_compile_success.png"
    ),
    ScreenshotState(
        "compile_error",
        "After failed compilation — red X visible",
        ["Press F7 to compile an AnimBP with errors"],
        ["compile_button_error"],
        "state_compile_error.png"
    ),
    ScreenshotState(
        "pin_drag_active",
        "Dragging a wire from a pin (connection in progress)",
        ["Click and hold on an output pin, drag without releasing"],
        ["pin_pose_output"],
        "state_pin_drag.png"
    ),
]


# ============================================================
# 5. QUERY INTERFACE
# ============================================================

class AnimGraphElements:
    """Query interface for element detection configuration."""

    @staticmethod
    def get_template_spec(name: str) -> TemplateSpec | None:
        return TEMPLATE_SPECS.get(name)

    @staticmethod
    def get_all_template_specs() -> dict[str, TemplateSpec]:
        return TEMPLATE_SPECS

    @staticmethod
    def get_color_signature(name: str) -> ColorSignature | None:
        return COLOR_SIGNATURES.get(name)

    @staticmethod
    def get_screenshot_states() -> list[ScreenshotState]:
        return SCREENSHOT_STATES

    @staticmethod
    def get_templates_for_region(region: str) -> list[TemplateSpec]:
        """Get all templates expected in a given editor region."""
        return [t for t in TEMPLATE_SPECS.values() if t.anchor_region == region]

    @staticmethod
    def get_missing_templates() -> list[TemplateSpec]:
        """Check which template images haven't been captured yet."""
        missing = []
        for spec in TEMPLATE_SPECS.values():
            template_path = TEMPLATE_DIR / spec.filename
            if not template_path.exists():
                missing.append(spec)
        return missing

    @staticmethod
    def get_capture_status() -> dict[str, bool]:
        """Return which templates exist vs missing."""
        status = {}
        for name, spec in TEMPLATE_SPECS.items():
            template_path = TEMPLATE_DIR / spec.filename
            status[name] = template_path.exists()
        return status

    @staticmethod
    def get_spatial_relations_for(element: str) -> list[SpatialRelation]:
        """Get all spatial relationships involving an element."""
        return [
            r for r in SPATIAL_RELATIONS
            if r.reference == element or r.target == element
        ]
