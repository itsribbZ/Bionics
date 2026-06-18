"""Tests for the native UE5.7 batch-retarget tool (ue5_batch_retarget_anims, M5 Stage 2).

Punch-list #1 + #3 (roadmap-coverage scan 2026-05-30): replaces the dead-API, RC-routed
ue5_batch_retarget with the UE5.7-correct IKRetargetBatchOperation.duplicate_and_retarget over
native :8090, ported from the live-proven recipe.

Unit-level: the :8090 bridge is mocked (fake_fire writes the result JSON) exactly like
tests/test_ue5_autorig.py. The front-to-back live retarget is scripts/livefire_retarget.py
(needs UE5 open) — the Sacred Rule #13 test.
"""
from __future__ import annotations

import json
from unittest.mock import patch

from core.bridge import ToolResult

DEAD_API = "IKRetargetBatchOperationNameRule"


# ============================================================================
# Registration
# ============================================================================


class TestRetargetRegistration:
    def test_tool_registered(self):
        from bionics_tools import ue5_retarget  # noqa: F401 — import registers
        from core.bridge import get_registry

        assert get_registry().get("ue5_batch_retarget_anims") is not None

    def test_destructive(self):
        from bionics_tools import ue5_retarget  # noqa: F401
        from core.bridge import SafetyTier, get_registry

        spec = get_registry().get("ue5_batch_retarget_anims")
        assert spec.safety_tier == SafetyTier.DESTRUCTIVE
        assert spec.annotations.destructive is True

    def test_aliases(self):
        from bionics_tools import ue5_retarget  # noqa: F401
        from core.bridge import get_registry

        spec = get_registry().get("ue5_batch_retarget_anims")
        assert "batch-retarget-anims" in spec.aliases
        assert "retarget-anims" in spec.aliases

    def test_script_uses_correct_ue57_api(self):
        from bionics_tools import ue5_retarget

        script = ue5_retarget._BATCH_RETARGET_SCRIPT
        assert DEAD_API not in script
        assert "duplicate_and_retarget(" in script
        assert "IKRetargeterController.get_controller" in script


# ============================================================================
# Validation
# ============================================================================


class TestRetargetValidation:
    def test_empty_retargeter_fails(self):
        from bionics_tools import ue5_retarget

        r = ue5_retarget.ue5_batch_retarget_anims(retargeter_path="", source_folder="/Game/A")
        assert r.ok is False
        assert "retargeter_path" in r.error

    def test_non_content_retargeter_fails(self):
        from bionics_tools import ue5_retarget

        r = ue5_retarget.ue5_batch_retarget_anims(
            retargeter_path="C:/x/RTG", source_folder="/Game/A"
        )
        assert r.ok is False

    def test_empty_source_folder_fails(self):
        from bionics_tools import ue5_retarget

        r = ue5_retarget.ue5_batch_retarget_anims(retargeter_path="/Game/RTG", source_folder="")
        assert r.ok is False
        assert "source_folder" in r.error

    def test_non_content_target_mesh_fails(self):
        from bionics_tools import ue5_retarget

        r = ue5_retarget.ue5_batch_retarget_anims(
            retargeter_path="/Game/RTG", source_folder="/Game/A", target_mesh_path="X:/m"
        )
        assert r.ok is False
        assert "target_mesh_path" in r.error


# ============================================================================
# Transport + fail-closed (mocked :8090)
# ============================================================================


class TestRetargetTransport:
    @staticmethod
    def _run(ue5_retarget, tmp_path, payload, **kwargs):
        captured = {}

        def fake_fire(tool_name, args):
            # capture the staged params for assertions
            try:
                captured["params"] = json.loads((tmp_path / "retarget_params.json").read_text())
            except Exception:  # noqa: BLE001
                pass
            (tmp_path / "retarget_result.json").write_text(json.dumps(payload), encoding="utf-8")
            return ToolResult.success(content="deferred")

        with patch.object(ue5_retarget, "_resolve_retarget_scratch_dir", return_value=tmp_path), \
             patch.object(ue5_retarget, "call_bridge_tool", side_effect=fake_fire), \
             patch("bionics_tools._ue5_native_exec.time.sleep"):
            call_args = {
                "retargeter_path": "/Game/Test/Skel/RTG_Mannequin_to_SW_HumanoidTemplate",
                "source_folder": "/Game/Characters/Mannequins/Animations/Manny",
                "suffix": "_SWHT",
            }
            call_args.update(kwargs)
            r = ue5_retarget.ue5_batch_retarget_anims(**call_args)
        return r, captured

    def test_success(self, tmp_path):
        from bionics_tools import ue5_retarget

        payload = {
            "ok": True, "stage": "done",
            "found_count": 12, "retargeted_count": 12,
            "retargeted": ["/Game/.../Walk_SWHT", "/Game/.../Run_SWHT"],
            "mapped_chains": 7, "mapped_chains_verified": True,
            "errors": [],
        }
        r, captured = self._run(ue5_retarget, tmp_path, payload)
        assert r.ok is True
        assert r.data["retargeted_count"] == 12
        assert r.data["mapped_chains"] == 7
        # suffix threaded into the staged params
        assert captured["params"]["suffix"] == "_SWHT"

    def test_fail_closed_zero_outputs(self, tmp_path):
        from bionics_tools import ue5_retarget

        payload = {
            "ok": False, "stage": "retarget",
            "found_count": 5, "retargeted_count": 0, "retargeted": [],
            "errors": ["duplicate_and_retarget produced 0 outputs (FAIL-CLOSED)"],
        }
        r, _ = self._run(ue5_retarget, tmp_path, payload)
        assert r.ok is False
        assert "0 outputs" in r.error

    def test_fail_closed_no_anims(self, tmp_path):
        from bionics_tools import ue5_retarget

        payload = {
            "ok": False, "stage": "collect_anims", "found_count": 0,
            "errors": ["no AnimSequence assets under /Game/Characters/Mannequins/Animations/Manny"],
        }
        r, _ = self._run(ue5_retarget, tmp_path, payload)
        assert r.ok is False
        assert "no AnimSequence" in r.error

    def test_fail_closed_unresolved_target_mesh(self, tmp_path):
        from bionics_tools import ue5_retarget

        payload = {
            "ok": False, "stage": "resolve_meshes",
            "errors": ["target SkeletalMesh could not be resolved (no override + no target preview mesh)"],
        }
        r, _ = self._run(ue5_retarget, tmp_path, payload)
        assert r.ok is False
        assert "target SkeletalMesh" in r.error

    def test_fail_closed_unmapped_chains(self, tmp_path):
        """When the chain query works AND min_mapped_chains is set, a low mapped count fails."""
        from bionics_tools import ue5_retarget

        payload = {
            "ok": False, "stage": "check_chains",
            "mapped_chains": 3, "mapped_chains_verified": True,
            "errors": ["only 3/7 target chains mapped to a source chain — retarget would T-pose unmapped chains (FAIL-CLOSED)"],
        }
        r, _ = self._run(ue5_retarget, tmp_path, payload, min_mapped_chains=7)
        assert r.ok is False
        assert "mapped" in r.error

    def test_bridge_unreachable_fails(self, tmp_path):
        from bionics_tools import ue5_retarget

        with patch.object(ue5_retarget, "_resolve_retarget_scratch_dir", return_value=tmp_path), \
             patch.object(ue5_retarget, "call_bridge_tool",
                          return_value=ToolResult.failure("Bridge unreachable")), \
             patch("bionics_tools._ue5_native_exec.time.sleep"):
            r = ue5_retarget.ue5_batch_retarget_anims(
                retargeter_path="/Game/RTG", source_folder="/Game/A"
            )
        assert r.ok is False
        assert "could not queue" in r.error

    def test_timeout_when_no_result(self, tmp_path):
        from bionics_tools import ue5_retarget

        seq = iter([0.0, 0.5] + [9999.0] * 20)
        with patch.object(ue5_retarget, "_resolve_retarget_scratch_dir", return_value=tmp_path), \
             patch.object(ue5_retarget, "call_bridge_tool", return_value=ToolResult.success(content="deferred")), \
             patch("bionics_tools._ue5_native_exec.time.sleep"), \
             patch("bionics_tools._ue5_native_exec.time.monotonic", side_effect=lambda: next(seq)):
            r = ue5_retarget.ue5_batch_retarget_anims(
                retargeter_path="/Game/RTG", source_folder="/Game/A", timeout_s=5.0
            )
        assert r.ok is False
        assert "Timed out" in r.error


# ============================================================================
# Preserved from the v0.8.4 pre-build stub (a4c6ac1) — these dead-API guards predate this
# session's tool build and are kept verbatim-in-spirit so no prior coverage is lost.
# ============================================================================


class TestDeadApiGuardsPreserved:
    def test_dead_api_not_used_in_repo_scratch(self):
        """The proven recipe (_run_m5_retarget_v2.py) must use duplicate_and_retarget, NOT the
        dead IKRetargetBatchOperationNameRule. Guards against regressing to the removed API.
        (Preserved from the original committed stub.)"""
        from pathlib import Path

        import pytest

        repo = Path(__file__).resolve().parent.parent
        v2 = repo / "_run_m5_retarget_v2.py"
        if not v2.exists():
            pytest.skip("_run_m5_retarget_v2.py scratch not present")
        text = v2.read_text(encoding="utf-8", errors="ignore")
        assert "duplicate_and_retarget" in text

    def test_abi_guard_flags_dead_namerule(self):
        """abi_guard must catch the dead retarget API anywhere in a planned call.
        (Preserved from the original committed stub — runs without the tool.)"""
        from core.abi_guard import analyze

        report = analyze(plan=[{"tool": "ue5_python", "args": {"script": "unreal.IKRetargetBatchOperationNameRule()"}}])
        assert any(v.rule == "S7.M" for v in report.violations)
        assert not report.ok
