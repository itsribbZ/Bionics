"""Tests for UE5 AnimGraph T-BRIDGE-1 wiring tools (v0.8.0).

Covers the 3 native bridge wrappers added to bionics_tools/ue5_animgraph.py
that close the AnimGraph manual-editor handoff gap documented in
sworder721 BUGS.md (T-BRIDGE-1, 2026-05-08):

  - ue5_set_bone_reference     (closes hole #1: FBoneReference structs)
  - ue5_bind_pin_to_property   (closes hole #2: pin → UPROPERTY binding)
  - ue5_splice_pose_flow       (closes hole #3: insert node into pose chain)

Backed by C++ tools at:
  - plugins/BionicsBridge/Source/BionicsBridgeEditor/Private/Tools/SetBoneReferenceTool.h
  - plugins/BionicsBridge/Source/BionicsBridgeEditor/Private/Tools/BindPinToPropertyTool.h
  - plugins/BionicsBridge/Source/BionicsBridgeEditor/Private/Tools/SplicePoseFlowTool.h
  - plugins/BionicsBridge/Source/BionicsBridgeEditor/Private/Tools/AnimGraphTools.cpp (impls)

Without a live UE5 bridge these tests verify:
  - All 3 wrappers register with correct category, safety tier, aliases
  - Each Python wrapper delegates to _call_tool with the right tool name + args
  - Defaults (bone_property='BoneToModify') round-trip correctly
"""

from unittest.mock import patch


def _mock_success(data: dict | None = None):
    from core.bridge import ToolResult
    if data is None:
        data = {"ok": True, "node_name": "MockNode_0"}
    return ToolResult.success(content="mocked", data=data)


def _mock_failure(msg: str = "mocked failure"):
    from core.bridge import ToolResult
    return ToolResult.failure(msg)


# ============================================================================
# Registration metadata
# ============================================================================


class TestTBridge1Registration:
    EXPECTED_TOOLS = (
        "ue5_set_bone_reference",
        "ue5_bind_pin_to_property",
        "ue5_splice_pose_flow",
    )

    def test_all_three_tbridge1_tools_registered(self):
        from bionics_tools import ue5_animgraph  # noqa: F401 — import registers
        from core.bridge import get_registry

        names = set(get_registry().list_names())
        missing = set(self.EXPECTED_TOOLS) - names
        assert not missing, f"Missing T-BRIDGE-1 tools: {missing}"

    def test_all_under_animgraph_category(self):
        from bionics_tools import ue5_animgraph  # noqa: F401
        from core.bridge import get_registry

        for name in self.EXPECTED_TOOLS:
            spec = get_registry().get(name)
            assert spec is not None
            assert spec.category == "ue5_animgraph", f"{name} category={spec.category}"

    def test_all_moderate_safety_tier(self):
        from bionics_tools import ue5_animgraph  # noqa: F401
        from core.bridge import SafetyTier, get_registry

        for name in self.EXPECTED_TOOLS:
            spec = get_registry().get(name)
            assert spec.safety_tier == SafetyTier.MODERATE, f"{name} safety={spec.safety_tier}"

    def test_set_bone_reference_aliases(self):
        from bionics_tools import ue5_animgraph  # noqa: F401
        from core.bridge import get_registry

        spec = get_registry().get("ue5_set_bone_reference")
        assert "set-bone-reference" in spec.aliases
        assert "set-bone-ref" in spec.aliases

    def test_splice_pose_flow_aliases(self):
        from bionics_tools import ue5_animgraph  # noqa: F401
        from core.bridge import get_registry

        spec = get_registry().get("ue5_splice_pose_flow")
        assert "splice-pose" in spec.aliases
        assert "splice-pose-flow" in spec.aliases


# ============================================================================
# set_bone_reference
# ============================================================================


class TestSetBoneReference:
    def test_calls_bridge_with_default_bone_property(self):
        """Default bone_property='BoneToModify' (most common — ModifyBone node)."""
        from bionics_tools import ue5_animgraph

        with patch(
            "bionics_tools.ue5_animgraph._call_tool",
            return_value=_mock_success(data={"ok": True, "bone_index": 42}),
        ) as mock:
            result = ue5_animgraph.ue5_set_bone_reference(
                asset_path="/Game/AnimBP/ABP_Trooper",
                node_name="AnimGraphNode_ModifyBone_0",
                bone_name="spine_01",
            )

        assert result.ok is True
        mock.assert_called_once_with("set_bone_reference", {
            "asset_path": "/Game/AnimBP/ABP_Trooper",
            "node_name": "AnimGraphNode_ModifyBone_0",
            "bone_property": "BoneToModify",
            "bone_name": "spine_01",
        })

    def test_passes_custom_bone_property(self):
        from bionics_tools import ue5_animgraph

        with patch(
            "bionics_tools.ue5_animgraph._call_tool",
            return_value=_mock_success(),
        ) as mock:
            ue5_animgraph.ue5_set_bone_reference(
                asset_path="/Game/AnimBP/ABP_X",
                node_name="AnimGraphNode_TwoBoneIK_0",
                bone_name="hand_l",
                bone_property="IKBone",
            )

        called_args = mock.call_args[0][1]
        assert called_args["bone_property"] == "IKBone"

    def test_returns_bridge_failure_unchanged(self):
        from bionics_tools import ue5_animgraph

        with patch(
            "bionics_tools.ue5_animgraph._call_tool",
            return_value=_mock_failure("Bone 'nonexistent' not found in skeleton"),
        ):
            result = ue5_animgraph.ue5_set_bone_reference(
                asset_path="/Game/AnimBP/ABP_X",
                node_name="ModifyBone_0",
                bone_name="nonexistent",
            )

        assert result.ok is False
        assert "not found in skeleton" in result.error


# ============================================================================
# bind_pin_to_property
# ============================================================================


class TestBindPinToProperty:
    def test_calls_bridge_with_correct_args(self):
        from bionics_tools import ue5_animgraph

        with patch(
            "bionics_tools.ue5_animgraph._call_tool",
            return_value=_mock_success(data={"ok": True, "source_type": "bool"}),
        ) as mock:
            result = ue5_animgraph.ue5_bind_pin_to_property(
                asset_path="/Game/AnimBP/ABP_Trooper",
                node_name="AnimGraphNode_BlendListByBool_0",
                pin_name="bActiveValue",
                source_variable="bIsCrouched",
            )

        assert result.ok is True
        mock.assert_called_once_with("bind_pin_to_property", {
            "asset_path": "/Game/AnimBP/ABP_Trooper",
            "node_name": "AnimGraphNode_BlendListByBool_0",
            "pin_name": "bActiveValue",
            "source_variable": "bIsCrouched",
        })

    def test_returns_bridge_failure_when_variable_missing(self):
        from bionics_tools import ue5_animgraph

        with patch(
            "bionics_tools.ue5_animgraph._call_tool",
            return_value=_mock_failure("Variable 'bMissingVar' not found in USWAnimInstance"),
        ):
            result = ue5_animgraph.ue5_bind_pin_to_property(
                asset_path="/Game/AnimBP/ABP_X",
                node_name="Node_0",
                pin_name="X",
                source_variable="bMissingVar",
            )

        assert result.ok is False
        assert "Variable 'bMissingVar' not found" in result.error


# ============================================================================
# splice_pose_flow
# ============================================================================


class TestSplicePoseFlow:
    def test_calls_bridge_with_correct_args(self):
        """Smoke-test the canonical UpperBody slot fix splice from BUGS.md walkthrough."""
        from bionics_tools import ue5_animgraph

        with patch(
            "bionics_tools.ue5_animgraph._call_tool",
            return_value=_mock_success(data={"ok": True, "broke_existing_wire": True}),
        ) as mock:
            result = ue5_animgraph.ue5_splice_pose_flow(
                asset_path="/Game/SciFITrooper_Man_03/Blueprints/AnimBP/ABP_SciFiTrooperManV3",
                source_node="AnimGraphNode_LocalToComponentSpace_0",
                source_pin="ComponentPose",
                sink_node="AnimGraphNode_Root",
                sink_pin="Result",
                splice_node="AnimGraphNode_LayeredBoneBlend_0",
                splice_input_pin="BasePose",
                splice_output_pin="Pose",
            )

        assert result.ok is True
        assert result.data["broke_existing_wire"] is True
        mock.assert_called_once_with("splice_pose_flow", {
            "asset_path": "/Game/SciFITrooper_Man_03/Blueprints/AnimBP/ABP_SciFiTrooperManV3",
            "source_node": "AnimGraphNode_LocalToComponentSpace_0",
            "source_pin": "ComponentPose",
            "sink_node": "AnimGraphNode_Root",
            "sink_pin": "Result",
            "splice_node": "AnimGraphNode_LayeredBoneBlend_0",
            "splice_input_pin": "BasePose",
            "splice_output_pin": "Pose",
        })

    def test_handles_first_time_wiring(self):
        """If no existing source→sink wire, broke_existing_wire=false (still ok=True)."""
        from bionics_tools import ue5_animgraph

        with patch(
            "bionics_tools.ue5_animgraph._call_tool",
            return_value=_mock_success(data={"ok": True, "broke_existing_wire": False}),
        ):
            result = ue5_animgraph.ue5_splice_pose_flow(
                asset_path="/Game/AnimBP/ABP_X",
                source_node="A", source_pin="Out",
                sink_node="B", sink_pin="In",
                splice_node="S", splice_input_pin="In", splice_output_pin="Out",
            )

        assert result.ok is True
        assert result.data["broke_existing_wire"] is False

    def test_returns_bridge_failure_on_missing_node(self):
        from bionics_tools import ue5_animgraph

        with patch(
            "bionics_tools.ue5_animgraph._call_tool",
            return_value=_mock_failure("Splice node not found: NotARealNode"),
        ):
            result = ue5_animgraph.ue5_splice_pose_flow(
                asset_path="/Game/AnimBP/ABP_X",
                source_node="A", source_pin="Out",
                sink_node="B", sink_pin="In",
                splice_node="NotARealNode",
                splice_input_pin="In", splice_output_pin="Out",
            )

        assert result.ok is False
        assert "Splice node not found" in result.error


# ============================================================================
# create_animgraph_variable_get + drive_animgraph_pin_via_variable
# (2026-05-15 campaign — the runtime-correct replacement for bind_pin)
# ============================================================================


class TestRuntimeDrivingToolsRegistration:
    EXPECTED = (
        "ue5_create_animgraph_variable_get",
        "ue5_drive_animgraph_pin_via_variable",
    )

    def test_registered_under_animgraph_moderate(self):
        from bionics_tools import ue5_animgraph  # noqa: F401 — import registers
        from core.bridge import SafetyTier, get_registry

        reg = get_registry()
        for name in self.EXPECTED:
            spec = reg.get(name)
            assert spec is not None, f"{name} not registered — planner can't route to it"
            assert spec.category == "ue5_animgraph", f"{name} category={spec.category}"
            assert spec.safety_tier == SafetyTier.MODERATE, f"{name} safety={spec.safety_tier}"


class TestCreateAnimGraphVariableGet:
    def test_calls_bridge_with_defaults(self):
        from bionics_tools import ue5_animgraph

        with patch(
            "bionics_tools.ue5_animgraph._call_tool",
            return_value=_mock_success(data={"ok": True, "node_name": "K2Node_VariableGet_0"}),
        ) as mock:
            result = ue5_animgraph.ue5_create_animgraph_variable_get(
                asset_path="/Game/AnimBP/ABP_Trooper",
                variable_name="bHasRangedWeapon",
            )

        assert result.ok is True
        mock.assert_called_once_with("create_animgraph_variable_get", {
            "asset_path": "/Game/AnimBP/ABP_Trooper",
            "variable_name": "bHasRangedWeapon",
            "pos_x": 0,
            "pos_y": 0,
        })


class TestDriveAnimGraphPinViaVariable:
    def test_calls_bridge_with_compile_default_true(self):
        """The canonical armed-loco fix: drive BlendListByBool.bActiveValue from a var."""
        from bionics_tools import ue5_animgraph

        with patch(
            "bionics_tools.ue5_animgraph._call_tool",
            return_value=_mock_success(data={"ok": True, "verified_linked": True, "compile_ok": True}),
        ) as mock:
            result = ue5_animgraph.ue5_drive_animgraph_pin_via_variable(
                asset_path="/Game/SciFITrooper_Man_03/Blueprints/AnimBP/ABP_SciFiTrooperManV3",
                variable_name="bHasRangedWeapon",
                target_node_name="BlendListByBool_0",
                target_pin_name="bActiveValue",
            )

        assert result.ok is True
        assert result.data["verified_linked"] is True
        mock.assert_called_once_with("drive_animgraph_pin_via_variable", {
            "asset_path": "/Game/SciFITrooper_Man_03/Blueprints/AnimBP/ABP_SciFiTrooperManV3",
            "variable_name": "bHasRangedWeapon",
            "target_node_name": "BlendListByBool_0",
            "target_pin_name": "bActiveValue",
            "pos_x": 0,
            "pos_y": 0,
            "compile": True,
        })

    def test_compile_false_defers(self):
        from bionics_tools import ue5_animgraph

        with patch(
            "bionics_tools.ue5_animgraph._call_tool",
            return_value=_mock_success(),
        ) as mock:
            ue5_animgraph.ue5_drive_animgraph_pin_via_variable(
                asset_path="/Game/AnimBP/ABP_X",
                variable_name="Alpha",
                target_node_name="Blend_0",
                target_pin_name="Alpha",
                compile=False,
            )

        assert mock.call_args[0][1]["compile"] is False

    def test_returns_bridge_failure_unchanged(self):
        from bionics_tools import ue5_animgraph

        with patch(
            "bionics_tools.ue5_animgraph._call_tool",
            return_value=_mock_failure("Variable 'nope' not found on AnimBP class"),
        ):
            result = ue5_animgraph.ue5_drive_animgraph_pin_via_variable(
                asset_path="/Game/AnimBP/ABP_X",
                variable_name="nope",
                target_node_name="N",
                target_pin_name="P",
            )

        assert result.ok is False
        assert "not found on AnimBP class" in result.error
