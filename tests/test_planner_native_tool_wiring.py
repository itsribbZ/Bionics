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


# ============================================================================
# Native-first ue5_python / existing_script execution (v0.8.1)
# ============================================================================


class TestNativeFirstPythonStep:
    """v0.8.1: planner ue5_python/existing_script steps run native :8090 FIRST, falling back
    to the RC bridge.execute_python ONLY when the native bridge is unreachable. Fixes the
    2026-05-29 live-fire finding — RC PythonScriptLibrary is blocked in UE5.7 ("Object
    Default__PythonScriptLibrary cannot be accessed remotely"), while the native game-thread
    bridge runs the script fine. Native path mocked via run_python_native (no UE5)."""

    def _planner(self):
        return AutoPlanner(ue5_project_path="", api_key="dummy")

    def test_native_reachable_short_circuits_rc(self):
        from unittest.mock import MagicMock
        bridge = MagicMock()
        with patch(
            "bionics_tools._ue5_native_exec.run_python_native",
            return_value={"reachable": True, "success": True, "output": "native-ok", "error": ""},
        ):
            success, output, error = self._planner()._execute_python_step(bridge, "import unreal")
        assert success is True
        assert output == "native-ok"
        assert error == ""
        bridge.execute_python.assert_not_called()  # native handled it — RC never touched

    def test_native_unreachable_falls_back_to_rc(self):
        from unittest.mock import MagicMock
        bridge = MagicMock()
        bridge.execute_python.return_value = MagicMock(
            success=True, error="", data={"output": [{"output": "rc-ok"}]}
        )
        with patch(
            "bionics_tools._ue5_native_exec.run_python_native",
            return_value={"reachable": False, "error": "bridge off"},
        ):
            success, output, error = self._planner()._execute_python_step(bridge, "import unreal")
        assert success is True
        assert output == "rc-ok"
        bridge.execute_python.assert_called_once()

    def test_native_failure_with_error_propagates(self):
        from unittest.mock import MagicMock
        bridge = MagicMock()
        with patch(
            "bionics_tools._ue5_native_exec.run_python_native",
            return_value={"reachable": True, "success": False, "output": "",
                          "error": "AttributeError: no attr 'foo'"},
        ):
            success, _output, error = self._planner()._execute_python_step(bridge, "unreal.foo()")
        assert success is False
        assert "AttributeError" in error
        bridge.execute_python.assert_not_called()  # a real native failure — do NOT retry dead RC

    def test_native_failure_empty_error_synthesizes(self):
        from unittest.mock import MagicMock
        bridge = MagicMock()
        with patch(
            "bionics_tools._ue5_native_exec.run_python_native",
            return_value={"reachable": True, "success": False, "output": "", "error": ""},
        ):
            success, _output, error = self._planner()._execute_python_step(bridge, "pass")
        assert success is False
        assert error != "", "empty native error must be replaced by a synthesized diagnostic"

    def test_ue5_python_plan_step_routes_through_native(self):
        """End-to-end: a ue5_python plan step is dispatched via the native-first helper."""
        from unittest.mock import MagicMock
        bridge = MagicMock()
        plan = {"steps": [{
            "index": 1, "execution_method": "ue5_python",
            "description": "spawn", "script_content": "import unreal\nunreal.log('hi')",
        }]}
        with patch(
            "bionics_tools._ue5_native_exec.run_python_native",
            return_value={"reachable": True, "success": True, "output": "hi", "error": ""},
        ):
            results = self._planner()._execute_plan_steps(plan, bridge)
        assert results[0]["success"] is True
        assert results[0]["output"] == "hi"
        bridge.execute_python.assert_not_called()

    def test_scratch_dir_failure_falls_back_to_rc(self):
        """A local-FS failure (scratch dir uncreatable) is NOT a bridge signal — it must fall
        back to RC, not be reported as a native failure. Regression guard for the v0.8.1
        review HIGH finding (scratch-None previously returned reachable=True, killing fallback)."""
        from unittest.mock import MagicMock
        bridge = MagicMock()
        bridge.execute_python.return_value = MagicMock(
            success=True, error="", data={"output": [{"output": "rc-ok"}]}
        )
        # Real run_python_native runs; only the scratch dir is forced to fail.
        with patch("bionics_tools._ue5_native_exec.resolve_scratch_dir", return_value=None):
            success, output, _error = self._planner()._execute_python_step(bridge, "import unreal")
        assert success is True
        assert output == "rc-ok"
        bridge.execute_python.assert_called_once()  # scratch-fail must fall through to RC


class TestRunPythonNative:
    """v0.8.1: the shared run_python_native wrapper — uuid-suffixed scratch names + cleanup."""

    def test_uuid_filenames_and_cleanup(self, tmp_path):
        from unittest.mock import MagicMock

        from bionics_tools import _ue5_native_exec as nx

        result_path = tmp_path / "step_result_deadbeef.json"

        def fake_fire(tool, args):
            # The game thread would write the result; simulate it for the uuid'd path.
            result_path.write_text('{"success": true, "output": "native-ran", "error": ""}',
                                   encoding="utf-8")
            return ToolResult.success(content="deferred")

        fake_uuid = MagicMock()
        fake_uuid.hex = "deadbeef00000000"  # -> uid "deadbeef"
        with patch.object(nx, "resolve_scratch_dir", return_value=tmp_path), \
             patch.object(nx.uuid, "uuid4", return_value=fake_uuid), \
             patch("bionics_tools._ue5_native_exec.time.sleep"):
            res = nx.run_python_native("import unreal", 5.0, invoke=fake_fire)

        assert res["reachable"] is True
        assert res["success"] is True
        assert res["output"] == "native-ran"
        # Best-effort cleanup removed every handshake file (no per-call accumulation).
        assert not list(tmp_path.glob("step_*")), "scratch handshake files must be cleaned up"
