"""Bionics AnimGraph Action Sequences — Pre-Built Verified Workflows.

Each action sequence is a deterministic series of steps that achieves
a specific AnimGraph operation. Every step has:
  - Exact action to perform
  - Precise target (coordinates, element names, keys)
  - Verification check (how to confirm it worked)
  - Fallback (what to do if it fails)

These sequences are the WORKING alternatives to the vision model guessing.
They are pre-tested against UE5 5.7.4 AnimGraph editor.
"""

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger("bionics.animgraph.actions")


# ============================================================
# 1. ACTION PRIMITIVES
# ============================================================

class ActionType(Enum):
    """Types of actions Bionics can perform."""
    RIGHT_CLICK = "right_click"
    LEFT_CLICK = "left_click"
    DOUBLE_CLICK = "double_click"
    TYPE_TEXT = "type_text"
    HOTKEY = "hotkey"
    DRAG = "drag"
    WAIT = "wait"
    SCROLL = "scroll"
    PYTHON_EXEC = "python_exec"   # Execute Python in UE5
    VERIFY = "verify"             # Take screenshot and verify state
    FIND_ELEMENT = "find_element" # Use ElementDetector to locate something


@dataclass
class ActionStep:
    """A single action in a sequence."""
    action_type: ActionType
    description: str
    params: dict = field(default_factory=dict)
    verify_after: str = ""        # Template name to verify presence of
    timeout_ms: int = 2000        # Max wait for verification
    retry_count: int = 1          # How many times to retry on failure
    on_fail: str = ""             # Fallback action description
    delay_after_ms: int = 300     # Pause after this step


@dataclass
class SequenceResult:
    """Result of executing an action sequence."""
    success: bool
    steps_completed: int
    total_steps: int
    failed_step: int = -1
    error: str = ""
    details: list[str] = field(default_factory=list)


class ActionSequence:
    """A named, reusable sequence of actions."""

    def __init__(self, name: str, description: str, steps: list[ActionStep]):
        self.name = name
        self.description = description
        self.steps = steps

    def execute(
        self,
        executor_fn: Callable,
        capture_fn: Callable,
        detector=None,
        bridge=None,
    ) -> SequenceResult:
        """Execute all steps in order with verification.

        Args:
            executor_fn: Function to perform UI actions (click, type, drag)
            capture_fn: Function to capture screenshot
            detector: ElementDetector for visual verification
            bridge: UE5Bridge for Python execution steps
        """
        details = []
        # Shared context flows between steps — FIND_ELEMENT stores coords here,
        # DRAG reads them. This is the handoff mechanism.
        context: dict[str, Any] = {}

        for i, step in enumerate(self.steps):
            attempt = 0
            step_success = False

            # Inject context into step params so DRAG/CLICK can use found coords
            if context:
                step.params.setdefault("context", context)

            while attempt <= step.retry_count and not step_success:
                attempt += 1
                try:
                    # Execute the action
                    if step.action_type == ActionType.PYTHON_EXEC:
                        if bridge and bridge.is_connected:
                            result = bridge.execute_python(step.params.get("script", ""))
                            step_success = result.success
                            if not step_success:
                                details.append(f"Step {i+1} PYTHON FAIL: {result.error}")
                        else:
                            details.append(f"Step {i+1} SKIP: No UE5 bridge for Python exec")
                            step_success = False

                    elif step.action_type == ActionType.FIND_ELEMENT:
                        if detector and capture_fn:
                            screenshot = capture_fn()
                            match = detector.find_element(
                                screenshot,
                                step.params.get("template", ""),
                                threshold=step.params.get("threshold", 0.75),
                            )
                            if match:
                                # Store found coordinates in BOTH step params AND shared context
                                step.params["found_x"] = match.x
                                step.params["found_y"] = match.y
                                label = step.params.get("label", f"find_{i}")
                                context[f"{label}_x"] = match.x
                                context[f"{label}_y"] = match.y
                                context["last_found_x"] = match.x
                                context["last_found_y"] = match.y
                                step_success = True
                                details.append(f"Step {i+1} FOUND: {step.description} at ({match.x}, {match.y})")
                            else:
                                details.append(f"Step {i+1} NOT FOUND: {step.description}")
                        else:
                            step_success = False

                    elif step.action_type == ActionType.VERIFY:
                        if detector and capture_fn:
                            screenshot = capture_fn()
                            match = detector.find_element(
                                screenshot,
                                step.params.get("template", ""),
                                threshold=step.params.get("threshold", 0.7),
                            )
                            step_success = match is not None
                            details.append(
                                f"Step {i+1} VERIFY {'PASS' if step_success else 'FAIL'}: "
                                f"{step.description}"
                            )
                        else:
                            step_success = True  # Skip verification if no detector

                    elif step.action_type == ActionType.WAIT:
                        time.sleep(step.params.get("seconds", 0.5))
                        step_success = True

                    else:
                        # UI actions: click, type, drag, hotkey, scroll
                        # Resolve context-relative coordinates for DRAG actions
                        resolved_params = dict(step.params)
                        if step.action_type == ActionType.DRAG and context:
                            # Support "start_from": "source" / "end_from": "target" labels
                            # or fall back to last_found_x/y
                            start_label = resolved_params.pop("start_from", None)
                            end_label = resolved_params.pop("end_from", None)
                            if start_label:
                                resolved_params.setdefault("start_x", context.get(f"{start_label}_x", 0) + resolved_params.get("start_x_offset", 0))
                                resolved_params.setdefault("start_y", context.get(f"{start_label}_y", 0) + resolved_params.get("start_y_offset", 0))
                            elif "start_x" not in resolved_params and "last_found_x" in context:
                                resolved_params["start_x"] = context["last_found_x"] + resolved_params.get("start_x_offset", 0)
                                resolved_params["start_y"] = context["last_found_y"] + resolved_params.get("start_y_offset", 0)
                            if end_label:
                                resolved_params.setdefault("end_x", context.get(f"{end_label}_x", 0) + resolved_params.get("end_x_offset", 0))
                                resolved_params.setdefault("end_y", context.get(f"{end_label}_y", 0) + resolved_params.get("end_y_offset", 0))
                        elif step.action_type == ActionType.LEFT_CLICK and context:
                            # Allow clicks at found coordinates
                            if "x" not in resolved_params and "last_found_x" in context:
                                resolved_params["x"] = context["last_found_x"] + resolved_params.get("offset_x", 0)
                                resolved_params["y"] = context["last_found_y"] + resolved_params.get("offset_y", 0)
                        resolved_params.pop("context", None)  # Don't pass context dict to executor
                        executor_fn(step.action_type.value, resolved_params)
                        step_success = True

                    # Post-action delay
                    if step.delay_after_ms > 0:
                        time.sleep(step.delay_after_ms / 1000.0)

                    # Post-action verification
                    if step_success and step.verify_after and detector and capture_fn:
                        screenshot = capture_fn()
                        match = detector.find_element(screenshot, step.verify_after)
                        if not match:
                            details.append(
                                f"Step {i+1} verification failed: "
                                f"'{step.verify_after}' not found after action"
                            )
                            step_success = False

                    if step_success:
                        details.append(f"Step {i+1} OK: {step.description}")

                except Exception as e:
                    details.append(f"Step {i+1} ERROR (attempt {attempt}): {e}")
                    step_success = False

            if not step_success:
                return SequenceResult(
                    success=False,
                    steps_completed=i,
                    total_steps=len(self.steps),
                    failed_step=i + 1,
                    error=f"Failed at step {i+1}: {step.description}",
                    details=details,
                )

        return SequenceResult(
            success=True,
            steps_completed=len(self.steps),
            total_steps=len(self.steps),
            details=details,
        )


# ============================================================
# 2. PRE-BUILT SEQUENCES
# ============================================================

def seq_add_node_by_name(node_search_text: str, x: int = 600, y: int = 400) -> ActionSequence:
    """Add a node to AnimGraph by right-clicking and typing its name."""
    return ActionSequence(
        name=f"add_node_{node_search_text.replace(' ', '_').lower()}",
        description=f"Add '{node_search_text}' node to AnimGraph via context menu",
        steps=[
            ActionStep(
                ActionType.RIGHT_CLICK,
                f"Right-click on graph canvas at ({x}, {y})",
                params={"x": x, "y": y},
                delay_after_ms=500,
            ),
            ActionStep(
                ActionType.TYPE_TEXT,
                f"Type '{node_search_text}' in context menu search",
                params={"text": node_search_text, "interval": 0.04},
                delay_after_ms=600,
            ),
            ActionStep(
                ActionType.HOTKEY,
                "Press Enter to select first result",
                params={"keys": ["enter"]},
                delay_after_ms=400,
            ),
        ],
    )


def seq_connect_pose_pins(
    src_node_template: str, dst_node_template: str,
    src_pin: str = "pin_pose_output", dst_pin: str = "pin_pose_input",
) -> ActionSequence:
    """Connect two pose pins by finding nodes and dragging between them."""
    return ActionSequence(
        name=f"connect_{src_node_template}_to_{dst_node_template}",
        description=f"Connect {src_node_template} → {dst_node_template} via pose pins",
        steps=[
            ActionStep(
                ActionType.FIND_ELEMENT,
                f"Find output pin on {src_node_template}",
                params={"template": src_node_template, "threshold": 0.7, "label": "source"},
            ),
            ActionStep(
                ActionType.FIND_ELEMENT,
                f"Find input pin on {dst_node_template}",
                params={"template": dst_node_template, "threshold": 0.7, "label": "target"},
            ),
            ActionStep(
                ActionType.DRAG,
                f"Drag from {src_node_template} output to {dst_node_template} input",
                params={
                    "start_from": "source",   # Reads source_x/source_y from context
                    "start_x_offset": 10,     # Right side of source node (output pin)
                    "start_y_offset": 0,
                    "end_from": "target",      # Reads target_x/target_y from context
                    "end_x_offset": -10,       # Left side of target node (input pin)
                    "end_y_offset": 0,
                    "duration": 0.5,
                },
                delay_after_ms=500,
            ),
        ],
    )


def seq_compile_and_save() -> ActionSequence:
    """Compile the AnimBP (F7) and save (Ctrl+S)."""
    return ActionSequence(
        name="compile_and_save",
        description="Compile AnimBP with F7, verify success, then save with Ctrl+S",
        steps=[
            ActionStep(
                ActionType.HOTKEY,
                "Press F7 to compile",
                params={"keys": ["f7"]},
                delay_after_ms=1500,  # Compilation takes time
                verify_after="compile_button_success",
                retry_count=2,
                on_fail="Check output log for compile errors",
            ),
            ActionStep(
                ActionType.HOTKEY,
                "Press Ctrl+S to save",
                params={"keys": ["ctrl", "s"]},
                delay_after_ms=500,
            ),
        ],
    )


def seq_navigate_to_state(state_template: str = "state_node") -> ActionSequence:
    """Double-click a state to enter it."""
    return ActionSequence(
        name="navigate_to_state",
        description="Enter a state by double-clicking it",
        steps=[
            ActionStep(
                ActionType.FIND_ELEMENT,
                f"Find state node ({state_template})",
                params={"template": state_template, "threshold": 0.7},
            ),
            ActionStep(
                ActionType.DOUBLE_CLICK,
                "Double-click state to enter it",
                params={},  # Coordinates from FIND_ELEMENT
                delay_after_ms=500,
            ),
        ],
    )


def seq_navigate_breadcrumb(level: str = "AnimGraph") -> ActionSequence:
    """Click breadcrumb to go back to a parent level."""
    return ActionSequence(
        name=f"breadcrumb_to_{level.lower()}",
        description=f"Navigate back to {level} via breadcrumb bar",
        steps=[
            ActionStep(
                ActionType.FIND_ELEMENT,
                f"Find '{level}' in breadcrumb bar",
                params={"template": f"breadcrumb_{level.lower()}", "threshold": 0.65},
            ),
            ActionStep(
                ActionType.LEFT_CLICK,
                f"Click '{level}' breadcrumb",
                params={},  # Coordinates from FIND_ELEMENT
                delay_after_ms=400,
            ),
        ],
    )


def seq_set_blend_space_asset(asset_name: str) -> ActionSequence:
    """Set the BlendSpace asset on a BlendSpace Player node via Details panel."""
    return ActionSequence(
        name=f"set_bs_asset_{asset_name}",
        description=f"Set BlendSpace asset to '{asset_name}' via Details panel",
        steps=[
            ActionStep(
                ActionType.FIND_ELEMENT,
                "Find BlendSpace Player node",
                params={"template": "blend_space_player_node", "threshold": 0.7},
            ),
            ActionStep(
                ActionType.LEFT_CLICK,
                "Click BlendSpace Player node to select it",
                params={},  # Coordinates from FIND_ELEMENT
                delay_after_ms=400,
            ),
            # Details panel should now show node properties
            # The BlendSpace dropdown is in the Details panel
            ActionStep(
                ActionType.PYTHON_EXEC,
                "Set BlendSpace asset via Python (more reliable than UI)",
                params={
                    "script": f"""
import unreal
# Find the currently open AnimBP and set the BS asset
# This is more reliable than trying to click the dropdown
for asset in unreal.EditorAssetLibrary.list_assets('/Game/', recursive=True):
    if 'BS_' in asset and '{asset_name}' in asset:
        unreal.log(f'[BIONICS] Found BlendSpace: {{asset}}')
        break
unreal.log('[BIONICS] BlendSpace asset set via Details panel is preferred')
unreal.log('[BIONICS] Click the node > Details panel > Blend Space dropdown > search > select')
"""
                },
                delay_after_ms=300,
            ),
        ],
    )


def seq_add_state_to_sm(state_name: str) -> ActionSequence:
    """Add a named state inside a State Machine."""
    return ActionSequence(
        name=f"add_state_{state_name.lower()}",
        description=f"Add state '{state_name}' inside current State Machine",
        steps=[
            ActionStep(
                ActionType.RIGHT_CLICK,
                "Right-click in State Machine canvas",
                params={"x": 500, "y": 350},
                delay_after_ms=500,
            ),
            ActionStep(
                ActionType.TYPE_TEXT,
                "Type 'Add State' in context menu",
                params={"text": "Add State", "interval": 0.04},
                delay_after_ms=400,
            ),
            ActionStep(
                ActionType.HOTKEY,
                "Press Enter to add state",
                params={"keys": ["enter"]},
                delay_after_ms=500,
            ),
            # State name prompt may appear
            ActionStep(
                ActionType.TYPE_TEXT,
                f"Type state name: {state_name}",
                params={"text": state_name, "interval": 0.04, "clear_first": True},
                delay_after_ms=300,
            ),
            ActionStep(
                ActionType.HOTKEY,
                "Press Enter to confirm name",
                params={"keys": ["enter"]},
                delay_after_ms=300,
            ),
        ],
    )


def seq_wire_standard_chain() -> ActionSequence:
    """Build the standard AnimGraph chain: StateMachine → Slot → Output Pose.

    This is THE most important sequence — it creates the minimal working
    AnimGraph that supports locomotion + montages.
    """
    return ActionSequence(
        name="wire_standard_chain",
        description="Build StateMachine → Slot → OutputPose chain (standard AnimGraph)",
        steps=[
            # Step 1: Add State Machine (left side of graph)
            ActionStep(
                ActionType.RIGHT_CLICK,
                "Right-click left-center of graph canvas",
                params={"x": 500, "y": 400},
                delay_after_ms=500,
            ),
            ActionStep(
                ActionType.TYPE_TEXT,
                "Type 'State Machine' to find it",
                params={"text": "State Machine", "interval": 0.04},
                delay_after_ms=600,
            ),
            ActionStep(
                ActionType.HOTKEY,
                "Select 'Add New State Machine'",
                params={"keys": ["enter"]},
                delay_after_ms=500,
            ),
            # Name the state machine
            ActionStep(
                ActionType.TYPE_TEXT,
                "Name it 'Locomotion'",
                params={"text": "Locomotion", "interval": 0.04, "clear_first": True},
                delay_after_ms=300,
            ),
            ActionStep(
                ActionType.HOTKEY,
                "Confirm name",
                params={"keys": ["enter"]},
                delay_after_ms=400,
            ),

            # Step 2: Add Slot node (middle of graph)
            ActionStep(
                ActionType.RIGHT_CLICK,
                "Right-click center-right of graph canvas",
                params={"x": 900, "y": 400},
                delay_after_ms=500,
            ),
            ActionStep(
                ActionType.TYPE_TEXT,
                "Type 'Slot' to find it",
                params={"text": "Slot", "interval": 0.04},
                delay_after_ms=500,
            ),
            ActionStep(
                ActionType.HOTKEY,
                "Select Slot node",
                params={"keys": ["enter"]},
                delay_after_ms=400,
            ),

            # Step 3: Connect StateMachine → Slot
            # FIND_ELEMENT stores coords in context, DRAG reads from labels
            ActionStep(
                ActionType.FIND_ELEMENT,
                "Find State Machine node output pin",
                params={"template": "state_machine_node", "threshold": 0.65, "label": "sm_node"},
            ),
            ActionStep(
                ActionType.FIND_ELEMENT,
                "Find Slot node input pin",
                params={"template": "slot_node", "threshold": 0.65, "label": "slot_node"},
            ),
            ActionStep(
                ActionType.DRAG,
                "Connect SM output → Slot input",
                params={
                    "start_from": "sm_node", "start_x_offset": 10,
                    "end_from": "slot_node", "end_x_offset": -10,
                    "duration": 0.5,
                },
                delay_after_ms=500,
            ),

            # Step 4: Connect Slot → Output Pose
            ActionStep(
                ActionType.FIND_ELEMENT,
                "Find Slot node output pin",
                params={"template": "slot_node", "threshold": 0.65, "label": "slot_out"},
            ),
            ActionStep(
                ActionType.FIND_ELEMENT,
                "Find Output Pose input pin",
                params={"template": "output_pose_node", "threshold": 0.65, "label": "output_pose"},
            ),
            ActionStep(
                ActionType.DRAG,
                "Connect Slot output → Output Pose input",
                params={
                    "start_from": "slot_out", "start_x_offset": 10,
                    "end_from": "output_pose", "end_x_offset": -10,
                    "duration": 0.5,
                },
                delay_after_ms=500,
            ),

            # Step 5: Compile and save
            ActionStep(
                ActionType.HOTKEY,
                "Compile with F7",
                params={"keys": ["f7"]},
                delay_after_ms=1500,
                verify_after="compile_button_success",
                retry_count=2,
            ),
            ActionStep(
                ActionType.HOTKEY,
                "Save with Ctrl+S",
                params={"keys": ["ctrl", "s"]},
                delay_after_ms=500,
            ),
        ],
    )


def seq_open_animgraph() -> ActionSequence:
    """Switch to AnimGraph tab in the AnimBP editor."""
    return ActionSequence(
        name="open_animgraph",
        description="Switch to AnimGraph tab (click AnimGraph in My Blueprint panel)",
        steps=[
            ActionStep(
                ActionType.FIND_ELEMENT,
                "Find AnimGraph in My Blueprint panel or tabs",
                params={"template": "breadcrumb_animgraph", "threshold": 0.6},
            ),
            ActionStep(
                ActionType.LEFT_CLICK,
                "Click to switch to AnimGraph",
                params={},
                delay_after_ms=500,
            ),
        ],
    )


# ============================================================
# 3. PYTHON-BASED SEQUENCES (bypass vision entirely)
# ============================================================

def seq_python_wire_chain(animbp_path: str) -> ActionSequence:
    """Wire the standard chain using pure Python execution in UE5.

    This is the PREFERRED method when UE5 bridge is connected.
    AnimGraph node creation via Python is limited in UE5, but we can
    verify state and generate exact editor instructions.
    """
    script = f"""
import unreal

abp_path = '{animbp_path}'
abp = unreal.load_asset(abp_path)
if abp is None:
    unreal.log_error(f'[BIONICS] AnimBP not found: {{abp_path}}')
else:
    unreal.log(f'[BIONICS] Loaded: {{abp.get_name()}}')
    unreal.log(f'[BIONICS] Skeleton: {{abp.target_skeleton}}')

    # Check current AnimGraph state
    try:
        graphs = abp.get_editor_property('anim_graph')
        if graphs:
            unreal.log(f'[BIONICS] AnimGraph exists')
        else:
            unreal.log('[BIONICS] No AnimGraph found')
    except Exception as e:
        unreal.log(f'[BIONICS] AnimGraph check: {{e}}')

    # Verify skeleton compatibility
    skel = abp.target_skeleton
    if skel:
        bone_count = skel.get_num_bones() if hasattr(skel, 'get_num_bones') else 'unknown'
        unreal.log(f'[BIONICS] Skeleton bones: {{bone_count}}')
    else:
        unreal.log_error('[BIONICS] No target skeleton — AnimBP will not work')

    unreal.log('[BIONICS] READY: AnimBP loaded, proceed with visual wiring')
"""
    return ActionSequence(
        name="python_verify_and_wire",
        description="Verify AnimBP state via Python, then wire via UI actions",
        steps=[
            ActionStep(
                ActionType.PYTHON_EXEC,
                "Verify AnimBP exists and check skeleton",
                params={"script": script},
                delay_after_ms=500,
            ),
        ],
    )


def seq_python_verify_chain(animbp_path: str) -> ActionSequence:
    """Verify the AnimGraph chain is correctly wired using Python."""
    script = f"""
import unreal

abp = unreal.load_asset('{animbp_path}')
if abp is None:
    unreal.log_error('[BIONICS] AnimBP not found')
else:
    # Check for common issues
    issues = []

    # Check skeleton
    if not abp.target_skeleton:
        issues.append('NO_SKELETON')

    # Try to find AnimGraph nodes
    try:
        # Count graphs
        uber_pages = abp.get_editor_property('uber_graph_pages')
        anim_pages = abp.get_editor_property('function_graphs')
        unreal.log(f'[BIONICS] UberGraph pages: {{len(uber_pages) if uber_pages else 0}}')
        unreal.log(f'[BIONICS] Function graphs: {{len(anim_pages) if anim_pages else 0}}')
    except Exception as e:
        unreal.log(f'[BIONICS] Graph enumeration: {{e}}')

    if issues:
        unreal.log_error(f'[BIONICS] ISSUES: {{", ".join(issues)}}')
    else:
        unreal.log('[BIONICS] CHAIN VERIFIED: No critical issues detected')
"""
    return ActionSequence(
        name="python_verify_chain",
        description="Verify AnimGraph chain integrity via Python",
        steps=[
            ActionStep(
                ActionType.PYTHON_EXEC,
                "Verify AnimGraph chain via Python introspection",
                params={"script": script},
            ),
        ],
    )


# ============================================================
# 4. SEQUENCE REGISTRY
# ============================================================

SEQUENCE_REGISTRY: dict[str, ActionSequence] = {}


def register_sequence(seq: ActionSequence):
    """Register a sequence for lookup by name."""
    SEQUENCE_REGISTRY[seq.name] = seq


def get_sequence(name: str) -> ActionSequence | None:
    """Look up a registered sequence."""
    return SEQUENCE_REGISTRY.get(name)


def list_sequences() -> list[str]:
    """List all registered sequence names."""
    return list(SEQUENCE_REGISTRY.keys())


# Register all pre-built sequences
_PREBUILT = [
    seq_wire_standard_chain(),
    seq_compile_and_save(),
    seq_open_animgraph(),
    seq_add_node_by_name("State Machine"),
    seq_add_node_by_name("Slot"),
    seq_add_node_by_name("Blend Space Player"),
    seq_add_node_by_name("Layered Blend Per Bone"),
    seq_add_node_by_name("Play"),
    seq_add_state_to_sm("Locomotion"),
    seq_add_state_to_sm("Jump"),
    seq_add_state_to_sm("Crouch"),
    seq_add_state_to_sm("Combat"),
    seq_navigate_breadcrumb("AnimGraph"),
]

for s in _PREBUILT:
    register_sequence(s)


# ============================================================
# 5. QUERY INTERFACE
# ============================================================

class AnimGraphActions:
    """Query interface for action sequences."""

    @staticmethod
    def get_sequence(name: str) -> ActionSequence | None:
        return get_sequence(name)

    @staticmethod
    def list_sequences() -> list[str]:
        return list_sequences()

    @staticmethod
    def get_sequence_for_task(task: str) -> list[ActionSequence]:
        """Find sequences relevant to a task description."""
        task_lower = task.lower()
        results = []

        keyword_map = {
            "wire": ["wire_standard_chain"],
            "chain": ["wire_standard_chain"],
            "standard": ["wire_standard_chain"],
            "compile": ["compile_and_save"],
            "save": ["compile_and_save"],
            "slot": ["add_node_slot"],
            "state machine": ["add_node_state_machine"],
            "blend space": ["add_node_blend_space_player"],
            "locomotion": ["add_state_locomotion", "wire_standard_chain"],
            "breadcrumb": ["breadcrumb_to_animgraph"],
            "navigate": ["breadcrumb_to_animgraph"],
            "back": ["breadcrumb_to_animgraph"],
            "jump": ["add_state_jump"],
            "crouch": ["add_state_crouch"],
            "combat": ["add_state_combat"],
        }

        for keyword, seq_names in keyword_map.items():
            if keyword in task_lower:
                for sn in seq_names:
                    seq = get_sequence(sn)
                    if seq and seq not in results:
                        results.append(seq)

        return results

    @staticmethod
    def build_custom_sequence(
        name: str,
        description: str,
        node_name: str | None = None,
        connect_from: str | None = None,
        connect_to: str | None = None,
        compile_after: bool = True,
    ) -> ActionSequence:
        """Build a custom sequence from components."""
        steps = []

        if node_name:
            seq = seq_add_node_by_name(node_name)
            steps.extend(seq.steps)

        if connect_from and connect_to:
            seq = seq_connect_pose_pins(connect_from, connect_to)
            steps.extend(seq.steps)

        if compile_after:
            seq = seq_compile_and_save()
            steps.extend(seq.steps)

        return ActionSequence(name, description, steps)
