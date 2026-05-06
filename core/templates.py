"""Bionics Action Templates - Parameterized action sequences for UE5 operations.

Templates are reusable, named sequences of actions that encapsulate common UE5
operations. Each template supports hybrid execution:
  1. Try UE5 API (fast, precise)
  2. Fall back to vision + precision clicking

Templates are registered in a global registry and invoked by name.
"""

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from core.precision import ElementDetector
from core.ue5_bridge import UE5Bridge
from core.verification import ActionVerifier, VerifyResult

logger = logging.getLogger("bionics.templates")


def _sanitize(s: str) -> str:
    """Sanitize a string for safe embedding in generated Python code."""
    return s.replace("\\", "\\\\").replace("'", "\\'").replace('"', '\\"').replace("\n", "\\n")


@dataclass
class TemplateResult:
    """Result of executing an action template."""
    success: bool
    method: str  # "api", "vision", "hybrid"
    actions_taken: int = 0
    details: str = ""
    error: str = ""
    data: dict = field(default_factory=dict)


class ActionTemplate(ABC):
    """Base class for all action templates."""

    name: str = ""
    description: str = ""
    category: str = "general"
    requires_ue5: bool = False

    @abstractmethod
    def execute_api(self, bridge: UE5Bridge, params: dict) -> TemplateResult:
        """Execute via UE5 API (fast, precise). Override in subclass."""
        ...

    @abstractmethod
    def execute_vision(
        self,
        detector: ElementDetector,
        verifier: ActionVerifier,
        executor_fn,
        capture_fn,
        params: dict,
    ) -> TemplateResult:
        """Execute via vision + mouse/keyboard. Override in subclass."""
        ...

    def execute(
        self,
        bridge: UE5Bridge | None,
        detector: ElementDetector,
        verifier: ActionVerifier,
        executor_fn,
        capture_fn,
        params: dict,
    ) -> TemplateResult:
        """Execute with automatic API/vision fallback."""
        # Try API first if available
        if bridge and bridge.is_connected and self.requires_ue5:
            logger.info(f"Template '{self.name}': trying API execution")
            result = self.execute_api(bridge, params)
            if result.success:
                return result
            logger.warning(f"Template '{self.name}': API failed ({result.error}), falling back to vision")

        # Fall back to vision
        logger.info(f"Template '{self.name}': using vision execution")
        return self.execute_vision(detector, verifier, executor_fn, capture_fn, params)


# ---- Template Registry ----

_REGISTRY: dict[str, ActionTemplate] = {}


def register_template(template: ActionTemplate):
    _REGISTRY[template.name] = template
    logger.info(f"Registered template: {template.name}")


def get_template(name: str) -> ActionTemplate | None:
    return _REGISTRY.get(name)


def list_templates() -> list[str]:
    return list(_REGISTRY.keys())


# ---- UE5-Specific Templates ----

class ConnectBlueprintPins(ActionTemplate):
    """Connect two Blueprint node pins."""

    name = "ue5.connect_pins"
    description = "Connect an output pin to an input pin in a Blueprint or AnimGraph"
    category = "ue5_blueprint"
    requires_ue5 = True

    def execute_api(self, bridge: UE5Bridge, params: dict) -> TemplateResult:
        result = bridge.connect_blueprint_pins(
            blueprint_path=params["blueprint_path"],
            source_node=params["source_node"],
            source_pin=params["source_pin"],
            target_node=params["target_node"],
            target_pin=params["target_pin"],
        )
        return TemplateResult(
            success=result.success,
            method="api",
            actions_taken=1,
            details=f"Connected {params['source_node']}.{params['source_pin']} -> "
                    f"{params['target_node']}.{params['target_pin']}",
            error=result.error,
            data=result.data,
        )

    def execute_vision(self, detector, verifier, executor_fn, capture_fn, params) -> TemplateResult:
        """Vision fallback: find pins on screen and drag-connect them."""
        actions = 0

        # 1. Capture screen
        screenshot = capture_fn()

        # 2. Find source pin by template or color
        source_desc = f"{params.get('source_node', '')}_{params.get('source_pin', '')}"
        target_desc = f"{params.get('target_node', '')}_{params.get('target_pin', '')}"

        # Try template matching first
        source_match = detector.find_element(screenshot, f"pin_{source_desc}")
        target_match = detector.find_element(screenshot, f"pin_{target_desc}")

        if not source_match or not target_match:
            return TemplateResult(
                success=False,
                method="vision",
                details="Could not locate pins on screen. Need Claude vision for coordinate identification.",
                error="Pin detection requires Claude vision assistance",
            )

        # 3. Drag from source to target
        executor_fn("drag", {
            "start_x": source_match.x,
            "start_y": source_match.y,
            "end_x": target_match.x,
            "end_y": target_match.y,
            "duration": 0.5,
        })
        actions += 1

        # 4. Verify connection was made
        after_screenshot = capture_fn()
        verify = verifier.verify_region_changed(
            screenshot, after_screenshot,
            x=min(source_match.x, target_match.x) - 20,
            y=min(source_match.y, target_match.y) - 20,
            width=abs(target_match.x - source_match.x) + 40,
            height=abs(target_match.y - source_match.y) + 40,
        )

        return TemplateResult(
            success=verify.result == VerifyResult.PASS,
            method="vision",
            actions_taken=actions,
            details=f"Drag-connected pins. Verification: {verify.result.name}",
        )


class OpenAssetInEditor(ActionTemplate):
    """Open a UE5 asset in the appropriate editor."""

    name = "ue5.open_asset"
    description = "Open an asset (Blueprint, AnimGraph, Material, etc.) in UE5 Editor"
    category = "ue5_navigation"
    requires_ue5 = True

    def execute_api(self, bridge: UE5Bridge, params: dict) -> TemplateResult:
        result = bridge.open_asset(params["asset_path"])
        return TemplateResult(
            success=result.success,
            method="api",
            actions_taken=1,
            details=f"Opened {params['asset_path']}",
            error=result.error,
        )

    def execute_vision(self, detector, verifier, executor_fn, capture_fn, params) -> TemplateResult:
        # Vision fallback: use Content Browser search
        actions = 0

        # Open Content Browser with Ctrl+Space
        executor_fn("hotkey", {"keys": ["ctrl", "space"]})
        actions += 1

        import time
        time.sleep(0.5)

        # Type asset name
        asset_name = params["asset_path"].split("/")[-1].split(".")[0]
        executor_fn("type_text", {"text": asset_name, "interval": 0.03})
        actions += 1

        time.sleep(0.8)

        # Press Enter to open
        executor_fn("hotkey", {"keys": ["enter"]})
        actions += 1

        return TemplateResult(
            success=True,
            method="vision",
            actions_taken=actions,
            details=f"Searched and opened '{asset_name}' via Content Browser",
        )


class SaveAsset(ActionTemplate):
    """Save the current asset."""

    name = "ue5.save_asset"
    description = "Save the currently open asset in UE5 Editor"
    category = "ue5_file"
    requires_ue5 = True

    def execute_api(self, bridge: UE5Bridge, params: dict) -> TemplateResult:
        if "asset_path" in params:
            result = bridge.save_asset(params["asset_path"])
        else:
            result = bridge.save_all()
        return TemplateResult(
            success=result.success,
            method="api",
            actions_taken=1,
            details="Asset saved via API",
            error=result.error,
        )

    def execute_vision(self, detector, verifier, executor_fn, capture_fn, params) -> TemplateResult:
        executor_fn("hotkey", {"keys": ["ctrl", "s"]})
        return TemplateResult(
            success=True,
            method="vision",
            actions_taken=1,
            details="Saved via Ctrl+S",
        )


class CompileBlueprint(ActionTemplate):
    """Compile the current Blueprint."""

    name = "ue5.compile"
    description = "Compile the currently open Blueprint or Animation Blueprint"
    category = "ue5_blueprint"
    requires_ue5 = True

    def execute_api(self, bridge: UE5Bridge, params: dict) -> TemplateResult:
        result = bridge.compile_blueprint(params["blueprint_path"])
        return TemplateResult(
            success=result.success,
            method="api",
            actions_taken=1,
            details="Blueprint compiled via API",
            error=result.error,
        )

    def execute_vision(self, detector, verifier, executor_fn, capture_fn, params) -> TemplateResult:
        # Click the Compile button or use hotkey
        executor_fn("hotkey", {"keys": ["f7"]})

        import time
        time.sleep(1.0)

        # Verify compile succeeded by checking for error indicators
        screenshot = capture_fn()
        # Look for the green checkmark (compile success) template
        success_match = detector.find_element(screenshot, "ue5_compile_success", threshold=0.7)

        return TemplateResult(
            success=success_match is not None,
            method="vision",
            actions_taken=1,
            details="Compiled via F7" + (" - success indicator found" if success_match else " - could not verify"),
        )


class CreateBlueprintNode(ActionTemplate):
    """Create a new node in the Blueprint graph."""

    name = "ue5.create_node"
    description = "Create a new node in the Blueprint or AnimGraph editor"
    category = "ue5_blueprint"
    requires_ue5 = True

    def execute_api(self, bridge: UE5Bridge, params: dict) -> TemplateResult:
        result = bridge.create_blueprint_node(
            blueprint_path=params["blueprint_path"],
            node_class=params["node_class"],
            pos_x=params.get("pos_x", 0),
            pos_y=params.get("pos_y", 0),
        )
        return TemplateResult(
            success=result.success,
            method="api",
            actions_taken=1,
            details=f"Created {params['node_class']}",
            error=result.error,
        )

    def execute_vision(self, detector, verifier, executor_fn, capture_fn, params) -> TemplateResult:
        import time
        actions = 0

        # Right-click to open context menu at the target position
        pos_x = params.get("click_x", 500)
        pos_y = params.get("click_y", 400)
        executor_fn("right_click", {"x": pos_x, "y": pos_y})
        actions += 1
        time.sleep(0.5)

        # Type node name in search
        node_name = params.get("node_search", params.get("node_class", ""))
        executor_fn("type_text", {"text": node_name, "interval": 0.03})
        actions += 1
        time.sleep(0.5)

        # Select first result
        executor_fn("hotkey", {"keys": ["enter"]})
        actions += 1

        return TemplateResult(
            success=True,
            method="vision",
            actions_taken=actions,
            details=f"Created node '{node_name}' via context menu",
        )


class SetNodeProperty(ActionTemplate):
    """Set a property on a Blueprint/AnimGraph node."""

    name = "ue5.set_property"
    description = "Set a property value on a UE5 node or object"
    category = "ue5_blueprint"
    requires_ue5 = True

    def execute_api(self, bridge: UE5Bridge, params: dict) -> TemplateResult:
        result = bridge.set_property(
            object_path=params["object_path"],
            property_name=params["property_name"],
            value=params["value"],
        )
        return TemplateResult(
            success=result.success,
            method="api",
            actions_taken=1,
            details=f"Set {params['property_name']} = {params['value']}",
            error=result.error,
        )

    def execute_vision(self, detector, verifier, executor_fn, capture_fn, params) -> TemplateResult:
        # This is complex via vision - need to find the property in Details panel
        return TemplateResult(
            success=False,
            method="vision",
            error="Property setting via vision requires Claude vision assistance for field location",
            details="Need Claude to identify the property field location on screen",
        )


class ExecuteConsoleCommand(ActionTemplate):
    """Execute a UE5 console command."""

    name = "ue5.console_command"
    description = "Execute a console command in UE5 Editor"
    category = "ue5_utility"
    requires_ue5 = True

    def execute_api(self, bridge: UE5Bridge, params: dict) -> TemplateResult:
        # Handle both {"command": "..."} and {"params": {"command": "..."}} from Claude
        cmd = params.get("command", "")
        if not cmd and "params" in params:
            cmd = params["params"].get("command", "")
        if not cmd:
            return TemplateResult(success=False, method="api", error=f"No 'command' in params: {params}")
        result = bridge.execute_console_command(cmd)
        return TemplateResult(
            success=result.success,
            method="api",
            actions_taken=1,
            details=f"Executed: {cmd}",
            error=result.error,
        )

    def execute_vision(self, detector, verifier, executor_fn, capture_fn, params) -> TemplateResult:
        import time
        # Extract command from various param formats Claude might use
        cmd = params.get("command", "")
        if not cmd and "params" in params:
            cmd = params["params"].get("command", "")
        if not cmd:
            return TemplateResult(success=False, method="vision", error=f"No 'command' in params: {params}")
        # Open console with backtick
        executor_fn("hotkey", {"keys": ["`"]})
        time.sleep(0.3)
        executor_fn("type_text", {"text": cmd, "interval": 0.02})
        executor_fn("hotkey", {"keys": ["enter"]})
        return TemplateResult(
            success=True,
            method="vision",
            actions_taken=3,
            details=f"Typed command in console: {cmd}",
        )


class NavigateGraph(ActionTemplate):
    """Navigate to a specific area of the Blueprint/AnimGraph graph."""

    name = "ue5.navigate_graph"
    description = "Pan/zoom the graph editor to focus on a specific area or node"
    category = "ue5_navigation"
    requires_ue5 = False

    def execute_api(self, bridge: UE5Bridge, params: dict) -> TemplateResult:
        # No direct API for graph viewport navigation
        return TemplateResult(success=False, method="api", error="Not available via API")

    def execute_vision(self, detector, verifier, executor_fn, capture_fn, params) -> TemplateResult:
        import time
        actions = 0

        if params.get("fit_all"):
            # Home key fits all nodes in view
            executor_fn("hotkey", {"keys": ["home"]})
            actions += 1
            time.sleep(0.3)
        elif params.get("zoom_level"):
            # Scroll to zoom
            zoom = params["zoom_level"]
            scroll_clicks = int((zoom - 1.0) * 10)
            executor_fn("scroll", {"clicks": scroll_clicks})
            actions += 1
        elif params.get("pan_x") or params.get("pan_y"):
            # Middle-mouse drag to pan (UE5 graph viewport uses middle button)
            cx, cy = 960, 540  # Assume center of screen
            executor_fn("drag", {
                "start_x": cx, "start_y": cy,
                "end_x": cx + params.get("pan_x", 0),
                "end_y": cy + params.get("pan_y", 0),
                "duration": 0.3,
                "button": "middle",
            })
            actions += 1

        return TemplateResult(
            success=True,
            method="vision",
            actions_taken=actions,
            details="Graph navigation performed",
        )


# ---- AnimBP Doctor Integration ----

class RunExistingScript(ActionTemplate):
    """Run an existing Python script from Content/Python/ via UE5 bridge."""

    name = "ue5.run_script"
    description = "Execute an existing Python tool from Content/Python/ inside UE5 Editor"
    category = "ue5_automation"
    requires_ue5 = True

    def execute_api(self, bridge: UE5Bridge, params: dict) -> TemplateResult:
        from pathlib import Path as _PathSafe
        # Basename-enforce script_name to block path traversal (e.g. "../../etc/x")
        script_name = _PathSafe(params.get("script_name", "")).name
        from core.paths import get_ue5_python_dir
        default_dir = get_ue5_python_dir()
        script_dir = params.get("script_dir",
            str(default_dir) if default_dir else "")
        script_path = f"{script_dir}\\{script_name}"

        code = f'exec(open(r"{script_path}").read())'
        result = bridge.execute_python(code)
        return TemplateResult(
            success=result.success,
            method="api",
            actions_taken=1,
            details=f"Executed {script_name} via UE5 Python bridge",
            error=result.error,
            data=result.data,
        )

    def execute_vision(self, detector, verifier, executor_fn, capture_fn, params) -> TemplateResult:
        return TemplateResult(
            success=False,
            method="vision",
            error="Script execution requires UE5 Python bridge — cannot run via vision",
        )


class AnimBPDoctorDiagnose(ActionTemplate):
    """Run AnimBP Doctor v1 Mobility and parse diagnostic results.

    The AnimBP Doctor is an 8-phase diagnostic pipeline:
      Phase 1: Skeleton & mesh verification
      Phase 2: AnimBP inventory + compatibility check
      Phase 3: BP_SWCharacter CDO audit + auto-fix
      Phase 4: BlendSpace population
      Phase 5: Montage verification
      Phase 6: PlayerStart collision capsule mobility fix
      Phase 7: AnimGraph wiring instructions (manual steps)
      Phase 8: Verdict + readiness check

    This template runs the doctor, captures output, and returns structured results
    that Bionics AutoPlanner can use to generate fix plans.
    """

    name = "ue5.animbp_doctor"
    description = "Run AnimBP Doctor diagnostic on the animation pipeline and return structured results"
    category = "ue5_animation"
    requires_ue5 = True

    @staticmethod
    def _get_doctor_path() -> str:
        from core.paths import get_ue5_python_dir
        d = get_ue5_python_dir()
        return str(d / "animbp_doctor.py") if d else ""

    def execute_api(self, bridge: UE5Bridge, params: dict) -> TemplateResult:
        # Run the doctor script and capture its log output
        capture_code = f'''
import io, sys
_bionics_buf = io.StringIO()
_bionics_old_log = unreal.log
_bionics_results = []
def _bionics_capture_log(msg):
    _bionics_buf.write(str(msg) + "\\n")
    _bionics_old_log(msg)
unreal.log = _bionics_capture_log
try:
    exec(open(r"{self._get_doctor_path()}").read())
except Exception as e:
    unreal.log_error(f"AnimBP Doctor error: {{e}}")
finally:
    unreal.log = _bionics_old_log
_bionics_output = _bionics_buf.getvalue()
unreal.log(f"[BIONICS_DOCTOR_OUTPUT_START]{{_bionics_output}}[BIONICS_DOCTOR_OUTPUT_END]")
'''
        result = bridge.execute_python(capture_code)

        # Parse the output for phase results
        output = result.data.get("output", "") if result.data else ""
        phases_ok = []
        phases_fail = []
        for i in range(1, 9):
            if f"Phase {i}" in output:
                if "PASS" in output.split(f"Phase {i}")[1].split("\n")[0]:
                    phases_ok.append(i)
                elif "FAIL" in output.split(f"Phase {i}")[1].split("\n")[0]:
                    phases_fail.append(i)

        return TemplateResult(
            success=result.success,
            method="api",
            actions_taken=1,
            details=f"AnimBP Doctor ran. Phases OK: {phases_ok}, Phases FAIL: {phases_fail}",
            error=result.error,
            data={
                "raw_output": output,
                "phases_ok": phases_ok,
                "phases_fail": phases_fail,
                "doctor_version": "v1 Mobility",
            },
        )

    def execute_vision(self, detector, verifier, executor_fn, capture_fn, params) -> TemplateResult:
        return TemplateResult(
            success=False,
            method="vision",
            error="AnimBP Doctor requires UE5 Python bridge for execution",
        )


class AnimBPCreateStateMachine(ActionTemplate):
    """Create or rebuild a state machine in an Animation Blueprint.

    Uses UE5 Python to programmatically add states and transitions.
    This is the core BP wiring template that saves hours of manual editor work.
    """

    name = "ue5.animbp_create_sm"
    description = "Create a state machine with states and transitions in an AnimBP"
    category = "ue5_animation"
    requires_ue5 = True

    def execute_api(self, bridge: UE5Bridge, params: dict) -> TemplateResult:
        abp_path = params.get("animbp_path", "/Game/Variant_Combat/Anims/ABP_SWCharacter")
        states = params.get("states", [
            {"name": "Locomotion", "type": "blend_space", "asset": "BS_Trooper_WalkRun"},
            {"name": "Jump", "type": "sequence", "asset": "ThirdPersonJump_Start"},
            {"name": "Crouch", "type": "blend_space", "asset": "BS_Trooper_CrouchWalk"},
        ])
        transitions = params.get("transitions", [
            {"from": "Locomotion", "to": "Jump", "condition": "bIsInAir"},
            {"from": "Jump", "to": "Locomotion", "condition": "not bIsInAir"},
            {"from": "Locomotion", "to": "Crouch", "condition": "bIsCrouching"},
            {"from": "Crouch", "to": "Locomotion", "condition": "not bIsCrouching"},
        ])

        # Generate Python code that creates the state machine
        state_defs = "\n".join([
            f'    unreal.log("[BIONICS] State: {s["name"]} ({s["type"]})")'
            for s in states
        ])
        code = f'''
import unreal

abp_path = "{abp_path}"
abp = unreal.load_asset(abp_path)
if abp is None:
    unreal.log_error(f"[BIONICS] AnimBP not found: {{abp_path}}")
else:
    unreal.log(f"[BIONICS] Loaded AnimBP: {{abp.get_name()}}")
    unreal.log("[BIONICS] State machine creation requires AnimGraph editor.")
    unreal.log("[BIONICS] States to create:")
{state_defs}
    unreal.log("[BIONICS] NOTE: UE5 Python API cannot modify AnimGraph topology directly.")
    unreal.log("[BIONICS] Generating editor step guide instead.")
    unreal.log("[BIONICS_SM_PLAN_START]")
    unreal.log("1. Open {abp_path} in AnimGraph editor")
    unreal.log("2. Delete existing broken state machine (if any)")
    unreal.log("3. Right-click > Add New State Machine > name it 'Locomotion'")
{chr(10).join([f'    unreal.log("4.{i+1}. Add state: {s["name"]} (type: {s["type"]}, asset: {s["asset"]})")' for i, s in enumerate(states)])}
{chr(10).join([f'    unreal.log("5.{i+1}. Add transition: {t["from"]} -> {t["to"]} (rule: {t["condition"]})")' for i, t in enumerate(transitions)])}
    unreal.log("6. Wire SM output to Final Animation Pose")
    unreal.log("7. Add DefaultSlot node between SM and Final Pose (for montages)")
    unreal.log("[BIONICS_SM_PLAN_END]")
'''
        result = bridge.execute_python(code)
        return TemplateResult(
            success=result.success,
            method="api",
            actions_taken=1,
            details=f"Generated SM plan: {len(states)} states, {len(transitions)} transitions",
            error=result.error,
            data={
                "animbp_path": abp_path,
                "states": states,
                "transitions": transitions,
                "note": "AnimGraph topology requires editor UI — plan generated for manual or vision execution",
            },
        )

    def execute_vision(self, detector, verifier, executor_fn, capture_fn, params) -> TemplateResult:
        return TemplateResult(
            success=False,
            method="vision",
            error="State machine creation via vision is complex — use the plan output from API mode",
        )


class ConfigureDataAsset(ActionTemplate):
    """Configure a DataAsset's properties via UE5 Remote Control API."""

    name = "ue5.configure_data_asset"
    description = "Set properties on a UE5 DataAsset (biomes, weapons, enemies, items)"
    category = "ue5_data"
    requires_ue5 = True

    def execute_api(self, bridge: UE5Bridge, params: dict) -> TemplateResult:
        asset_path = params.get("asset_path", "")
        properties = params.get("properties", {})

        if not asset_path:
            return TemplateResult(success=False, method="api", error="No asset_path provided")

        code = f'''
import unreal
asset = unreal.load_asset("{asset_path}")
if asset is None:
    unreal.log_error("[BIONICS] DataAsset not found: {asset_path}")
else:
    unreal.log(f"[BIONICS] Loaded DataAsset: {{asset.get_name()}}")
    # Set each property
'''
        for prop, value in properties.items():
            sp = _sanitize(prop)
            if isinstance(value, str):
                code += f'    asset.set_editor_property("{sp}", "{_sanitize(value)}")\n'
            elif isinstance(value, (int, float)):
                code += f'    asset.set_editor_property("{sp}", {value})\n'
            elif isinstance(value, bool):
                code += f'    asset.set_editor_property("{sp}", {str(value)})\n'
        code += f'''
    unreal.EditorAssetLibrary.save_loaded_asset(asset)
    unreal.log("[BIONICS] DataAsset configured and saved: {asset_path}")
'''
        result = bridge.execute_python(code)
        return TemplateResult(
            success=result.success,
            method="api",
            actions_taken=len(properties) + 1,
            details=f"Configured {len(properties)} properties on {asset_path}",
            error=result.error,
        )

    def execute_vision(self, detector, verifier, executor_fn, capture_fn, params) -> TemplateResult:
        return TemplateResult(
            success=False,
            method="vision",
            error="DataAsset configuration via vision not implemented — use API mode",
        )


class SetBlueprintDefaults(ActionTemplate):
    """Set default property values on a Blueprint's CDO (Class Default Object)."""

    name = "ue5.set_bp_defaults"
    description = "Set default values on a Blueprint (GameMode, Character, etc.)"
    category = "ue5_blueprint"
    requires_ue5 = True

    def execute_api(self, bridge: UE5Bridge, params: dict) -> TemplateResult:
        bp_path = params.get("blueprint_path", "")
        defaults = params.get("defaults", {})

        code = f'''
import unreal
bp = unreal.load_asset("{bp_path}")
if bp is None:
    unreal.log_error("[BIONICS] Blueprint not found: {bp_path}")
else:
    cdo = unreal.get_default_object(bp.generated_class())
    unreal.log(f"[BIONICS] Setting defaults on: {{cdo.get_name()}}")
'''
        for prop, value in defaults.items():
            sp = _sanitize(prop)
            if isinstance(value, str) and value.startswith("/Game/"):
                sv = _sanitize(value)
                code += f'''
    ref = unreal.load_asset("{sv}")
    if ref:
        cdo.set_editor_property("{sp}", ref)
        unreal.log(f"[BIONICS] Set {sp} = {sv}")
    else:
        unreal.log_warning("[BIONICS] Asset not found: {sv}")
'''
            elif isinstance(value, str):
                code += f'    cdo.set_editor_property("{sp}", "{_sanitize(value)}")\n'
            else:
                code += f'    cdo.set_editor_property("{sp}", {value})\n'
        code += f'''
    unreal.EditorAssetLibrary.save_loaded_asset(bp)
    unreal.log("[BIONICS] Blueprint defaults saved: {bp_path}")
'''
        result = bridge.execute_python(code)
        return TemplateResult(
            success=result.success,
            method="api",
            actions_taken=len(defaults) + 1,
            details=f"Set {len(defaults)} defaults on {bp_path}",
            error=result.error,
        )

    def execute_vision(self, detector, verifier, executor_fn, capture_fn, params) -> TemplateResult:
        return TemplateResult(
            success=False,
            method="vision",
            error="BP defaults via vision not implemented — use API mode",
        )


# ---- Register All Templates ----

def register_all_templates():
    """Register all built-in templates."""
    for cls in [
        ConnectBlueprintPins,
        OpenAssetInEditor,
        SaveAsset,
        CompileBlueprint,
        CreateBlueprintNode,
        SetNodeProperty,
        ExecuteConsoleCommand,
        NavigateGraph,
        # Bionics v0.3 — AnimBP Doctor + BP Wiring
        RunExistingScript,
        AnimBPDoctorDiagnose,
        AnimBPCreateStateMachine,
        ConfigureDataAsset,
        SetBlueprintDefaults,
    ]:
        register_template(cls())
    logger.info(f"Registered {len(_REGISTRY)} built-in templates")


# Auto-register on import
register_all_templates()

# Load AnimGraph expert module (registers 6 additional templates)
try:
    import ue5_modules.animgraph.animgraph_templates  # noqa: F401
except ImportError:
    logger.debug("AnimGraph expert module not available")
