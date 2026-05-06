"""Bionics AnimGraph Templates — ActionTemplate subclasses for AnimGraph operations.

These extend the core template system (core/templates.py) with
AnimGraph-specific operations backed by the knowledge base,
element detection, and pre-built action sequences.

Registered automatically when this module is imported.
"""

import logging

from core.templates import (
    ActionTemplate,
    TemplateResult,
    register_template,
)
from core.ue5_bridge import UE5Bridge
from ue5_modules.animgraph.action_sequences import (
    seq_add_node_by_name,
    seq_compile_and_save,
    seq_python_verify_chain,
    seq_wire_standard_chain,
)
from ue5_modules.animgraph.element_templates import AnimGraphElements
from ue5_modules.animgraph.knowledge_base import ANIMGRAPH_NODES, AnimGraphKB

logger = logging.getLogger("bionics.animgraph.templates")


# ============================================================
# AnimGraph-Specific ActionTemplates
# ============================================================

class AnimGraphWireStandardChain(ActionTemplate):
    """Wire the standard AnimGraph chain: StateMachine → Slot → Output Pose.

    This is the #1 most important AnimGraph operation. It creates the
    minimal working pipeline that supports locomotion + montages.

    Execution priority:
    1. Python API: verify state, generate instructions
    2. Vision: use pre-built action sequence with element detection
    """

    name = "ue5.animgraph.wire_chain"
    description = "Wire standard AnimGraph chain (SM → Slot → Output Pose)"
    category = "ue5_animgraph"
    requires_ue5 = True

    def execute_api(self, bridge: UE5Bridge, params: dict) -> TemplateResult:
        abp_path = params.get("animbp_path", "/Game/Variant_Combat/Anims/ABP_SW_MasterTemplate")

        # Step 1: Verify AnimBP exists and get state
        verify_script = f"""
import unreal

abp = unreal.load_asset('{abp_path}')
if abp is None:
    unreal.log_error('[BIONICS] AnimBP not found: {abp_path}')
else:
    unreal.log(f'[BIONICS] AnimBP loaded: {{abp.get_name()}}')
    unreal.log(f'[BIONICS] Skeleton: {{abp.target_skeleton}}')

    # Open in editor
    unreal.EditorAssetLibrary.open_editor_for_assets(['{abp_path}'])
    unreal.log('[BIONICS] Opened AnimBP in editor')

    # Provide exact wiring instructions
    unreal.log('[BIONICS] === WIRING INSTRUCTIONS ===')
    unreal.log('[BIONICS] 1. Click AnimGraph tab (left panel)')
    unreal.log('[BIONICS] 2. Right-click canvas → Add New State Machine → name "Locomotion"')
    unreal.log('[BIONICS] 3. Right-click canvas → search "Slot" → add Slot node')
    unreal.log('[BIONICS] 4. Connect: StateMachine OUT → Slot IN (white pose pins)')
    unreal.log('[BIONICS] 5. Connect: Slot OUT → Output Pose IN')
    unreal.log('[BIONICS] 6. F7 to compile, Ctrl+S to save')
    unreal.log('[BIONICS] === CHAIN: SM → Slot(DefaultSlot) → Output Pose ===')
"""
        result = bridge.execute_python(verify_script)

        return TemplateResult(
            success=result.success,
            method="api",
            actions_taken=1,
            details="AnimBP opened and wiring instructions generated. "
                    "Visual wiring sequence needed for graph topology changes.",
            error=result.error,
            data={
                "animbp_path": abp_path,
                "chain": "StateMachine → Slot(DefaultSlot) → Output Pose",
                "requires_vision": True,
                "knowledge_base": AnimGraphKB.get_standard_chain(),
            },
        )

    def execute_vision(self, detector, verifier, executor_fn, capture_fn, params) -> TemplateResult:
        # Use the pre-built action sequence
        sequence = seq_wire_standard_chain()
        result = sequence.execute(executor_fn, capture_fn, detector)

        return TemplateResult(
            success=result.success,
            method="vision",
            actions_taken=result.steps_completed,
            details=f"Standard chain wiring: {result.steps_completed}/{result.total_steps} steps. "
                    + "; ".join(result.details[-3:]),
            error=result.error,
        )


class AnimGraphAddNode(ActionTemplate):
    """Add a specific node to the AnimGraph via context menu search.

    Uses the knowledge base to validate the node exists and get the
    correct search text for the context menu.
    """

    name = "ue5.animgraph.add_node"
    description = "Add a named node to the AnimGraph (right-click → search → add)"
    category = "ue5_animgraph"
    requires_ue5 = False  # Can work via vision alone

    def execute_api(self, bridge: UE5Bridge, params: dict) -> TemplateResult:
        # Can't add AnimGraph nodes via API — this is a vision-only operation
        node_name = params.get("node_name", "")
        node_info = AnimGraphKB.get_node(node_name)

        if not node_info:
            return TemplateResult(
                success=False, method="api",
                error=f"Unknown node type: {node_name}. "
                      f"Known types: {', '.join(ANIMGRAPH_NODES.keys())}",
            )

        return TemplateResult(
            success=False, method="api",
            error="AnimGraph nodes can only be added via editor UI (vision mode required)",
            data={
                "node_info": {
                    "class": node_info.class_name,
                    "search_text": node_info.search_name,
                    "category": node_info.category.value,
                    "notes": node_info.notes,
                },
                "requires_vision": True,
            },
        )

    def execute_vision(self, detector, verifier, executor_fn, capture_fn, params) -> TemplateResult:
        node_name = params.get("node_name", "")
        node_info = AnimGraphKB.get_node(node_name)

        if not node_info:
            return TemplateResult(
                success=False, method="vision",
                error=f"Unknown node type: {node_name}",
            )

        search_text = node_info.search_name
        if not search_text:
            return TemplateResult(
                success=False, method="vision",
                error=f"Node '{node_name}' cannot be added via context menu "
                      f"(e.g., Output Pose always exists)",
            )

        x = params.get("x", 600)
        y = params.get("y", 400)
        sequence = seq_add_node_by_name(search_text, x, y)
        result = sequence.execute(executor_fn, capture_fn, detector)

        return TemplateResult(
            success=result.success,
            method="vision",
            actions_taken=result.steps_completed,
            details=f"Added '{node_info.display_name}' via context menu search '{search_text}'",
            error=result.error,
        )


class AnimGraphVerifyChain(ActionTemplate):
    """Verify the AnimGraph chain is correctly wired."""

    name = "ue5.animgraph.verify"
    description = "Verify AnimGraph chain integrity and report issues"
    category = "ue5_animgraph"
    requires_ue5 = True

    def execute_api(self, bridge: UE5Bridge, params: dict) -> TemplateResult:
        abp_path = params.get("animbp_path", "/Game/Variant_Combat/Anims/ABP_SW_MasterTemplate")
        sequence = seq_python_verify_chain(abp_path)
        result = sequence.execute(None, None, None, bridge)

        return TemplateResult(
            success=result.success,
            method="api",
            actions_taken=result.steps_completed,
            details="; ".join(result.details),
            error=result.error,
        )

    def execute_vision(self, detector, verifier, executor_fn, capture_fn, params) -> TemplateResult:
        # Visual verification: look for the standard chain elements
        screenshot = capture_fn()
        found = {}

        for elem_name in ["output_pose_node", "slot_node", "state_machine_node"]:
            match = detector.find_element(screenshot, elem_name, threshold=0.65)
            found[elem_name] = match is not None

        chain_complete = all(found.values())
        missing = [k for k, v in found.items() if not v]

        return TemplateResult(
            success=chain_complete,
            method="vision",
            actions_taken=1,
            details=f"Chain check: {found}. {'COMPLETE' if chain_complete else f'MISSING: {missing}'}",
            error="" if chain_complete else f"Missing elements: {missing}",
            data={"elements_found": found},
        )


class AnimGraphCompileAndSave(ActionTemplate):
    """Compile and save the AnimBP."""

    name = "ue5.animgraph.compile_save"
    description = "Compile AnimBP (F7) and save (Ctrl+S) with verification"
    category = "ue5_animgraph"
    requires_ue5 = False

    def execute_api(self, bridge: UE5Bridge, params: dict) -> TemplateResult:
        abp_path = params.get("animbp_path", "")
        if abp_path:
            result = bridge.compile_blueprint(abp_path)
            if result.success:
                save_result = bridge.save_asset(abp_path)
                return TemplateResult(
                    success=save_result.success,
                    method="api",
                    actions_taken=2,
                    details=f"Compiled and saved {abp_path}",
                )
            return TemplateResult(
                success=False, method="api",
                error=f"Compile failed: {result.error}",
            )
        return TemplateResult(success=False, method="api", error="No animbp_path provided")

    def execute_vision(self, detector, verifier, executor_fn, capture_fn, params) -> TemplateResult:
        sequence = seq_compile_and_save()
        result = sequence.execute(executor_fn, capture_fn, detector)

        return TemplateResult(
            success=result.success,
            method="vision",
            actions_taken=result.steps_completed,
            details="; ".join(result.details),
            error=result.error,
        )


class AnimGraphQueryKB(ActionTemplate):
    """Query the AnimGraph Knowledge Base for information.

    This is a non-destructive, information-only template.
    Returns node info, rules, workflow steps, or connection validation.
    """

    name = "ue5.animgraph.query"
    description = "Query AnimGraph knowledge base (nodes, rules, workflows, pin validation)"
    category = "ue5_animgraph"
    requires_ue5 = False

    def execute_api(self, bridge: UE5Bridge, params: dict) -> TemplateResult:
        return self._query(params)

    def execute_vision(self, detector, verifier, executor_fn, capture_fn, params) -> TemplateResult:
        return self._query(params)

    def _query(self, params: dict) -> TemplateResult:
        query_type = params.get("query_type", "node")
        query = params.get("query", "")

        if query_type == "node":
            node = AnimGraphKB.get_node(query)
            if node:
                return TemplateResult(
                    success=True, method="kb",
                    details=f"{node.display_name}: {node.notes}",
                    data={
                        "class": node.class_name,
                        "search": node.search_name,
                        "inputs": [p.name for p in node.input_pins],
                        "outputs": [p.name for p in node.output_pins],
                        "properties": node.key_properties,
                        "notes": node.notes,
                    },
                )
            return TemplateResult(success=False, method="kb", error=f"Node not found: {query}")

        elif query_type == "rules":
            rules = AnimGraphKB.get_rules(query)
            return TemplateResult(
                success=bool(rules), method="kb",
                details=f"{len(rules)} rules for '{query}'",
                data={"rules": rules},
            )

        elif query_type == "workflow":
            wf = AnimGraphKB.get_workflow(query)
            if wf:
                return TemplateResult(
                    success=True, method="kb",
                    details=f"Workflow '{wf.name}': {len(wf.steps)} steps",
                    data={
                        "name": wf.name,
                        "description": wf.description,
                        "prerequisite": wf.prerequisite,
                        "steps": [{"action": s.action, "target": s.target, "verify": s.verification}
                                  for s in wf.steps],
                        "result": wf.result,
                    },
                )
            return TemplateResult(success=False, method="kb", error=f"Workflow not found: {query}")

        elif query_type == "validate_connection":
            src_node = params.get("source_node", "")
            src_pin = params.get("source_pin", "")
            dst_node = params.get("target_node", "")
            dst_pin = params.get("target_pin", "")
            valid, msg = AnimGraphKB.validate_connection(src_node, src_pin, dst_node, dst_pin)
            return TemplateResult(
                success=valid, method="kb",
                details=msg,
                data={"valid": valid, "message": msg},
            )

        elif query_type == "task":
            nodes = AnimGraphKB.get_node_for_task(query)
            return TemplateResult(
                success=bool(nodes), method="kb",
                details=f"Recommended nodes for '{query}': "
                        + ", ".join(n.display_name for n in nodes),
                data={"recommended": [n.display_name for n in nodes]},
            )

        elif query_type == "chain":
            return TemplateResult(
                success=True, method="kb",
                details=AnimGraphKB.get_standard_chain(),
            )

        return TemplateResult(success=False, method="kb", error=f"Unknown query type: {query_type}")


class AnimGraphCaptureStatus(ActionTemplate):
    """Check the status of reference screenshot/template captures."""

    name = "ue5.animgraph.capture_status"
    description = "Check which AnimGraph reference templates have been captured"
    category = "ue5_animgraph"
    requires_ue5 = False

    def execute_api(self, bridge: UE5Bridge, params: dict) -> TemplateResult:
        return self._check()

    def execute_vision(self, detector, verifier, executor_fn, capture_fn, params) -> TemplateResult:
        return self._check()

    def _check(self) -> TemplateResult:
        status = AnimGraphElements.get_capture_status()
        captured = sum(1 for v in status.values() if v)
        total = len(status)
        missing = [k for k, v in status.items() if not v]

        return TemplateResult(
            success=captured > 0,
            method="local",
            details=f"Templates: {captured}/{total} captured. "
                    + (f"Missing: {', '.join(missing[:5])}" if missing else "All captured!"),
            data={"status": status, "captured": captured, "total": total, "missing": missing},
        )


# ============================================================
# Registration
# ============================================================

def register_animgraph_templates():
    """Register all AnimGraph templates in the global registry."""
    for cls in [
        AnimGraphWireStandardChain,
        AnimGraphAddNode,
        AnimGraphVerifyChain,
        AnimGraphCompileAndSave,
        AnimGraphQueryKB,
        AnimGraphCaptureStatus,
    ]:
        register_template(cls())
    logger.info("Registered 6 AnimGraph templates")


# Auto-register on import
register_animgraph_templates()
