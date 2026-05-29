"""Tests for wiring native-first tools into the AutoPlanner / divine_powers pipeline.

Verifies the planner (a) advertises the bionics_tool execution method + the preferred
native tools so Claude emits bionics_tool steps, and (b) dispatches those steps to the
@bionics_tool registry at execution time. All offline — no UE5, no Claude API.

Closes the documented gap: working native tools (uasvc/autorig/drive_animgraph_pin) were
registered but UNREACHABLE from the planner, which only emitted ue5_python/existing_script.
"""

from unittest.mock import patch

from core.auto_planner import PLANNER_SYSTEM_PROMPT, PREFERRED_NATIVE_TOOLS, AutoPlanner
from core.bridge import ToolResult


class _FakeSpec:
    def __init__(self, fn):
        self.fn = fn


class _FakeRegistry:
    def __init__(self, tools):
        self._t = tools

    def get(self, name):
        return self._t.get(name)


# ============================================================================
# Plan-generation awareness
# ============================================================================


class TestPlannerAwareness:
    def test_prompt_advertises_bionics_tool_method(self):
        assert "bionics_tool" in PLANNER_SYSTEM_PROMPT
        # The step schema must expose where the tool name + args go.
        assert "bionics_args" in PLANNER_SYSTEM_PROMPT

    def test_preferred_native_tools_block_lists_proven_tools(self):
        for name in (
            "ue5_uasvc_import_skeletal",
            "ue5_uasvc_preflight",
            "ue5_autorig_humanoid",
            "ue5_drive_animgraph_pin_via_variable",
        ):
            assert name in PREFERRED_NATIVE_TOOLS, f"{name} not surfaced to the planner"


# ============================================================================
# Registry dispatch (_invoke_bionics_tool)
# ============================================================================


class TestInvokeBionicsTool:
    def test_success_normalizes_toolresult(self):
        ap = AutoPlanner()
        spec = _FakeSpec(lambda **kw: ToolResult.success(content="did it", data={"x": 1}))
        with patch("core.bridge.get_registry", return_value=_FakeRegistry({"my_tool": spec})):
            res = ap._invoke_bionics_tool("my_tool", {"a": 1})
        assert res["success"] is True
        assert "did it" in res["output"]
        assert res["data"] == {"x": 1}

    def test_failure_toolresult_propagates(self):
        ap = AutoPlanner()
        spec = _FakeSpec(lambda **kw: ToolResult.failure("boom"))
        with patch("core.bridge.get_registry", return_value=_FakeRegistry({"t": spec})):
            res = ap._invoke_bionics_tool("t", {})
        assert res["success"] is False
        assert "boom" in res["error"]

    def test_unknown_tool(self):
        ap = AutoPlanner()
        # Empty fake registry on both the initial get and the post-register_all retry.
        with patch("core.bridge.get_registry", return_value=_FakeRegistry({})):
            res = ap._invoke_bionics_tool("nope_not_a_tool", {})
        assert res["success"] is False
        assert "Unknown bionics tool" in res["error"]

    def test_arg_mismatch_is_caught(self):
        ap = AutoPlanner()

        def needs_x(x):
            return ToolResult.success()

        with patch("core.bridge.get_registry", return_value=_FakeRegistry({"t": _FakeSpec(needs_x)})):
            res = ap._invoke_bionics_tool("t", {"y": 2})
        assert res["success"] is False
        assert "arg mismatch" in res["error"]


# ============================================================================
# Execution dispatch (_execute_plan_steps)
# ============================================================================


class TestExecuteDispatch:
    def test_bionics_tool_step_dispatched(self):
        ap = AutoPlanner()
        plan = {
            "steps": [
                {
                    "index": 1,
                    "execution_method": "bionics_tool",
                    "bionics_tool": "ue5_uasvc_import_skeletal",
                    "bionics_args": {"file_path": "x.glb", "asset_name": "SK_X"},
                    "description": "import skeletal",
                }
            ]
        }
        with patch.object(
            ap,
            "_invoke_bionics_tool",
            return_value={"success": True, "output": "imported", "error": "", "data": {}},
        ) as m:
            results = ap._execute_plan_steps(plan, None)

        m.assert_called_once_with("ue5_uasvc_import_skeletal", {"file_path": "x.glb", "asset_name": "SK_X"})
        assert results[0]["step"] == 1
        assert results[0]["success"] is True

    def test_bionics_tool_step_without_name_falls_through(self):
        """A bionics_tool method with no tool name hits the catch-all (not dispatched)."""
        ap = AutoPlanner()
        plan = {"steps": [{"index": 1, "execution_method": "bionics_tool", "description": "noop"}]}
        with patch.object(ap, "_invoke_bionics_tool") as m:
            results = ap._execute_plan_steps(plan, None)
        m.assert_not_called()
        assert results[0]["success"] is None
