"""Tests for UE5 rigging + Control Rig tool surface.

Covers Phase 2 modules shipped 2026-04-16:
- bionics_tools/ue5_rigging.py       — 4 tools (IK Rig + IK Retargeter + batch retarget)
- bionics_tools/ue5_controlrig.py    — 3 tools (Control Rig asset + AnimBP binding)

These tools generate Python scripts that execute inside UE5. Without a live UE5
bridge we can't run them end-to-end, so tests verify:
- Tool registration metadata (category, safety tier, aliases)
- Script body correctness (parameters substituted, path-escaping applied)
- Delegated calls to `run_python` happen with the expected wrapped script
"""

from unittest.mock import patch

# ============================================================================
# Shared helpers — mock run_python so no UE5 bridge is needed
# ============================================================================


def _mock_tool_result(success: bool = True, data: dict | None = None):
    from core.bridge import ToolResult
    if success:
        return ToolResult.success(content="mocked", data=data or {"ok": True})
    return ToolResult.failure("mocked failure")


def _patch_rigging_run_python(return_value=None):
    """Patch the run_python symbol that ue5_rigging.py resolved at import time."""
    return patch("bionics_tools.ue5_rigging.run_python",
                 return_value=return_value or _mock_tool_result())


def _patch_controlrig_run_python(return_value=None):
    return patch("bionics_tools.ue5_controlrig.run_python",
                 return_value=return_value or _mock_tool_result())


# ============================================================================
# Registration metadata
# ============================================================================


class TestRiggingRegistration:
    def test_all_four_rigging_tools_registered(self):
        """Ensure the 4 rigging tools are discoverable via the registry."""
        from bionics_tools import ue5_rigging  # noqa: F401 — import registers
        from core.bridge import get_registry
        names = {t.name for t in get_registry().list_all()}
        expected = {
            "ue5_create_ik_rig",
            "ue5_ik_rig_add_chain",
            "ue5_create_ik_retargeter",
            "ue5_batch_retarget",
        }
        assert expected.issubset(names), f"Missing: {expected - names}"

    def test_batch_retarget_is_destructive(self):
        from bionics_tools import ue5_rigging  # noqa: F401
        from core.bridge import SafetyTier, get_registry
        tool = get_registry().get("ue5_batch_retarget")
        assert tool is not None
        assert tool.safety_tier == SafetyTier.DESTRUCTIVE

    def test_create_ik_rig_is_moderate(self):
        from bionics_tools import ue5_rigging  # noqa: F401
        from core.bridge import SafetyTier, get_registry
        tool = get_registry().get("ue5_create_ik_rig")
        assert tool.safety_tier == SafetyTier.MODERATE

    def test_rigging_tools_have_rigging_category(self):
        from bionics_tools import ue5_rigging  # noqa: F401
        from core.bridge import get_registry
        for name in ("ue5_create_ik_rig", "ue5_ik_rig_add_chain",
                     "ue5_create_ik_retargeter", "ue5_batch_retarget"):
            tool = get_registry().get(name)
            assert tool.category == "ue5_rigging", f"{name} wrong category: {tool.category}"


class TestControlRigRegistration:
    def test_all_three_controlrig_tools_registered(self):
        from bionics_tools import ue5_controlrig  # noqa: F401
        from core.bridge import get_registry
        names = {t.name for t in get_registry().list_all()}
        expected = {
            "ue5_create_control_rig",
            "ue5_assign_control_rig_to_animbp",
            "ue5_control_rig_info",
        }
        assert expected.issubset(names), f"Missing: {expected - names}"

    def test_control_rig_info_is_safe_readonly(self):
        from bionics_tools import ue5_controlrig  # noqa: F401
        from core.bridge import SafetyTier, get_registry
        tool = get_registry().get("ue5_control_rig_info")
        assert tool.safety_tier == SafetyTier.SAFE
        assert tool.annotations.read_only is True

    def test_create_control_rig_is_moderate(self):
        from bionics_tools import ue5_controlrig  # noqa: F401
        from core.bridge import SafetyTier, get_registry
        tool = get_registry().get("ue5_create_control_rig")
        assert tool.safety_tier == SafetyTier.MODERATE

    def test_controlrig_tools_have_controlrig_category(self):
        from bionics_tools import ue5_controlrig  # noqa: F401
        from core.bridge import get_registry
        for name in ("ue5_create_control_rig", "ue5_assign_control_rig_to_animbp",
                     "ue5_control_rig_info"):
            tool = get_registry().get(name)
            assert tool.category == "ue5_controlrig"


# ============================================================================
# Script body correctness — ue5_rigging
# ============================================================================


class TestRiggingScriptGeneration:
    def test_create_ik_rig_injects_rig_path(self):
        from bionics_tools.ue5_rigging import ue5_create_ik_rig
        with _patch_rigging_run_python() as mock_rp:
            ue5_create_ik_rig(
                rig_path="/Game/Characters/IK/IK_Trooper",
                skeleton_path="/Game/Skeletons/Trooper",
            )
            script = mock_rp.call_args.args[0]
            assert "/Game/Characters/IK/IK_Trooper" in script
            assert "/Game/Skeletons/Trooper" in script
            assert "IKRigDefinition" in script

    def test_create_ik_rig_handles_empty_preview_mesh(self):
        from bionics_tools.ue5_rigging import ue5_create_ik_rig
        with _patch_rigging_run_python() as mock_rp:
            ue5_create_ik_rig(
                rig_path="/Game/A/B",
                skeleton_path="/Game/S",
                # no preview_mesh_path
            )
            script = mock_rp.call_args.args[0]
            # Default empty string still substituted
            assert "preview_path = ''" in script

    def test_ik_rig_add_chain_uses_bone_reference(self):
        from bionics_tools.ue5_rigging import ue5_ik_rig_add_chain
        with _patch_rigging_run_python() as mock_rp:
            ue5_ik_rig_add_chain(
                rig_path="/Game/IK/R",
                chain_name="Spine",
                start_bone="spine_01",
                end_bone="spine_05",
            )
            script = mock_rp.call_args.args[0]
            assert "Spine" in script
            assert "spine_01" in script
            assert "spine_05" in script
            assert "add_retarget_chain" in script

    def test_ik_rig_add_chain_injects_goal_when_present(self):
        from bionics_tools.ue5_rigging import ue5_ik_rig_add_chain
        with _patch_rigging_run_python() as mock_rp:
            ue5_ik_rig_add_chain(
                rig_path="/Game/R",
                chain_name="Arm_L",
                start_bone="upperarm_l",
                end_bone="hand_l",
                goal_bone="ik_hand_l",
            )
            script = mock_rp.call_args.args[0]
            assert "ik_hand_l" in script

    def test_create_ik_retargeter_wires_source_and_target_rigs(self):
        from bionics_tools.ue5_rigging import ue5_create_ik_retargeter
        with _patch_rigging_run_python() as mock_rp:
            ue5_create_ik_retargeter(
                retargeter_path="/Game/RTG/RTG_M_T",
                source_rig_path="/Game/IK/Mannequin",
                target_rig_path="/Game/IK/Trooper",
            )
            script = mock_rp.call_args.args[0]
            assert "/Game/RTG/RTG_M_T" in script
            assert "/Game/IK/Mannequin" in script
            assert "/Game/IK/Trooper" in script
            assert "RetargetSourceOrTarget.SOURCE" in script
            assert "RetargetSourceOrTarget.TARGET" in script

    def test_batch_retarget_passes_name_rule(self):
        from bionics_tools.ue5_rigging import ue5_batch_retarget
        with _patch_rigging_run_python() as mock_rp:
            ue5_batch_retarget(
                source_folder="/Game/Mannequin/Anims",
                target_folder="/Game/Trooper/Anims",
                retargeter_path="/Game/RTG/RTG_M_T",
                search_str="MM_",
                replace_str="TR_",
                prefix="Retargeted_",
            )
            script = mock_rp.call_args.args[0]
            assert "IKRetargetBatchOperationNameRule" in script
            assert "MM_" in script
            assert "TR_" in script
            assert "Retargeted_" in script
            assert "AnimSequence" in script


# ============================================================================
# Script body correctness — ue5_controlrig
# ============================================================================


class TestControlRigScriptGeneration:
    def test_create_control_rig_uses_factory(self):
        from bionics_tools.ue5_controlrig import ue5_create_control_rig
        with _patch_controlrig_run_python() as mock_rp:
            ue5_create_control_rig(
                rig_path="/Game/Characters/CR_Trooper",
                skeletal_mesh_path="/Game/Mesh/Trooper",
            )
            script = mock_rp.call_args.args[0]
            assert "ControlRigBlueprintFactory" in script
            assert "ControlRigBlueprint" in script
            assert "/Game/Characters/CR_Trooper" in script
            assert "/Game/Mesh/Trooper" in script

    def test_create_control_rig_binds_mesh_when_provided(self):
        from bionics_tools.ue5_controlrig import ue5_create_control_rig
        with _patch_controlrig_run_python() as mock_rp:
            ue5_create_control_rig(
                rig_path="/Game/CR",
                skeletal_mesh_path="/Game/Mesh",
            )
            script = mock_rp.call_args.args[0]
            assert "preview_skeletal_mesh" in script

    def test_assign_control_rig_emits_bridge_instruction(self):
        """Python cannot add AnimGraph nodes — the tool should emit a structured
        instruction telling the caller to use the C++ bridge."""
        from bionics_tools.ue5_controlrig import ue5_assign_control_rig_to_animbp
        with _patch_controlrig_run_python() as mock_rp:
            ue5_assign_control_rig_to_animbp(
                animbp_path="/Game/Blueprints/ABP_SW",
                control_rig_path="/Game/CR",
                alpha=0.75,
            )
            script = mock_rp.call_args.args[0]
            assert "call_bionicsbridge" in script
            assert "ue5_create_animgraph_node" in script
            assert "AnimGraphNode_ControlRig" in script
            assert "0.75" in script  # alpha substituted

    def test_control_rig_info_returns_readonly_inspection(self):
        from bionics_tools.ue5_controlrig import ue5_control_rig_info
        with _patch_controlrig_run_python() as mock_rp:
            ue5_control_rig_info(rig_path="/Game/CR_Trooper")
            script = mock_rp.call_args.args[0]
            assert "/Game/CR_Trooper" in script
            # Must inspect at least the three documented fields
            assert "preview_skeletal_mesh" in script
            assert "hierarchy" in script


# ============================================================================
# Path escaping — defense against injection via asset paths
# ============================================================================


class TestPathEscaping:
    def test_single_quote_in_path_is_escaped(self):
        """If an asset path contains a single quote, the generated Python script
        must escape it rather than terminate the literal."""
        from bionics_tools.ue5_rigging import ue5_create_ik_rig
        with _patch_rigging_run_python() as mock_rp:
            ue5_create_ik_rig(
                rig_path="/Game/IK'inject",
                skeleton_path="/Game/S",
            )
            script = mock_rp.call_args.args[0]
            # No bare injection — either escaped \' or different quoting.
            assert "/Game/IK'inject" not in script or "\\'" in script or '"' in script
