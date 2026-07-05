"""Tests for the abi-guard pre-execution gate in AutoPlanner (punch-list #4, 2026-05-30).

The gate runs core.abi_guard.analyze over each project-mutating plan step BEFORE it
executes and fail-closes on a BLOCK-level S7 violation (e.g. the dead UE5.7
IKRetargetBatchOperationNameRule). WARN/INFO violations log but never block. Unit-level:
no UE5, no bridge — abi_guard is pure static analysis.
"""
from __future__ import annotations

import sys

from core.auto_planner import AutoPlanner

DEAD_API = "IKRetargetBatchOperationNameRule"


class TestAbiGateStep:
    def test_clean_ue5_python_passes(self):
        ap = AutoPlanner()
        step = {"execution_method": "ue5_python", "script_content": "import unreal\nunreal.log('ok')"}
        assert ap._abi_gate_step("ue5_python", step, step["script_content"]) is None

    def test_dead_api_in_script_blocks(self):
        ap = AutoPlanner()
        script = f"batch = unreal.{DEAD_API}()"
        step = {"execution_method": "ue5_python", "script_content": script}
        res = ap._abi_gate_step("ue5_python", step, script)
        assert res is not None
        assert res["success"] is False
        assert "S7.M" in res["error"]
        assert res["note"] == "abi_guard pre-execution gate blocked this step"

    def test_dead_api_in_bionics_args_blocks(self):
        ap = AutoPlanner()
        step = {
            "execution_method": "bionics_tool",
            "bionics_tool": "ue5_run_python",
            "bionics_args": {"script": f"unreal.{DEAD_API}()"},
        }
        res = ap._abi_gate_step("bionics_tool", step, "")
        assert res is not None
        assert res["success"] is False
        assert DEAD_API in res["error"] or "S7.M" in res["error"]

    def test_bind_pin_warns_but_does_not_block(self):
        """bind_pin_to_property is a WARN (S7.C metadata-only), not a BLOCK — must not gate."""
        ap = AutoPlanner()
        step = {
            "execution_method": "bionics_tool",
            "bionics_tool": "bind_pin_to_property",
            "bionics_args": {"pin": "X", "property": "Y"},
        }
        assert ap._abi_gate_step("bionics_tool", step, "") is None

    def test_cpp_edit_method_not_gated(self):
        """cpp_edit is filtered upstream as a patch-hint; the gate itself returns None for it."""
        ap = AutoPlanner()
        step = {"execution_method": "cpp_edit", "script_content": f"// uses {DEAD_API}"}
        assert ap._abi_gate_step("cpp_edit", step, step["script_content"]) is None

    def test_guard_unavailable_is_nonfatal(self, monkeypatch):
        """If core.abi_guard cannot be imported, the gate returns None (never freezes a plan)."""
        ap = AutoPlanner()
        monkeypatch.setitem(sys.modules, "core.abi_guard", None)  # makes `import` raise
        script = f"unreal.{DEAD_API}()"
        step = {"execution_method": "ue5_python", "script_content": script}
        assert ap._abi_gate_step("ue5_python", step, script) is None


class TestAbiGateInExecuteLoop:
    def test_execute_plan_steps_blocks_dead_api(self):
        """Full _execute_plan_steps: a dead-API ue5_python step is blocked fail-closed before
        ever touching the bridge (bridge=None proves it never executes)."""
        ap = AutoPlanner()
        plan = {"steps": [{
            "index": 1,
            "execution_method": "ue5_python",
            "description": "retarget anims",
            "script_content": f"batch = unreal.{DEAD_API}()",
        }]}
        results = ap._execute_plan_steps(plan, bridge=None)
        assert len(results) == 1
        assert results[0]["success"] is False
        assert "abi-guard BLOCK" in results[0]["error"]
        assert results[0]["note"] == "abi_guard pre-execution gate blocked this step"
        assert results[0]["step"] == 1

    def test_execute_plan_steps_ue5_python_patch_hint_not_gated(self):
        """A ue5_python C++ patch-hint (description prefixed [C++ EDIT]) is filtered as a hint
        BEFORE the gate, so even if its comment text mentions a dead API it records as
        'pending agent' (success=None), never abi-guard-blocked."""
        ap = AutoPlanner()
        plan = {"steps": [{
            "index": 1,
            "execution_method": "ue5_python",
            "description": f"[C++ EDIT] remove the {DEAD_API} call",
            "script_content": f"# replace {DEAD_API} with duplicate_and_retarget",
        }]}
        results = ap._execute_plan_steps(plan, bridge=None)
        assert len(results) == 1
        assert results[0]["success"] is None
        assert "patch hint" in results[0]["note"].lower()

    def test_execute_plan_steps_cpp_edit_passthrough_unaffected(self):
        """A cpp_edit-method step falls to the else branch (success=None, 'requires Bionics
        agent') — the gate returns None for non-(ue5_python/bionics_tool) methods, so this
        existing behavior is unchanged."""
        ap = AutoPlanner()
        plan = {"steps": [{
            "index": 1,
            "execution_method": "cpp_edit",
            "description": "edit header",
            "script_content": "// add a UPROPERTY",
        }]}
        results = ap._execute_plan_steps(plan, bridge=None)
        assert len(results) == 1
        assert results[0]["success"] is None
        assert "requires Bionics agent" in results[0]["note"]
