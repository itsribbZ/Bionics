"""Tests for the standalone read-only skeleton bone validator + its MVPDoctor ASSET check.

Punch-list #2 (roadmap-coverage scan 2026-05-30): the fail-closed 23-bone gate was FUSED
into ue5_autorig_humanoid (ran only when a plan emitted an autorig step). This extracts a
validate-only tool (no rig side-effect) and wires it into MVPDoctor's demo_ready sweep as a
bridge-gated Category.ASSET check.

Unit-level: the :8090 bridge is mocked (fake_fire writes the result JSON), exactly like
tests/test_ue5_autorig.py. The front-to-back live validate is the autorig live-fire (UE5 open).
"""
from __future__ import annotations

import json
from unittest.mock import patch

from core.bridge import ToolResult

# ============================================================================
# Tool registration
# ============================================================================


class TestValidateRegistration:
    def test_tool_registered(self):
        from bionics_tools import ue5_autorig  # noqa: F401 — import registers
        from core.bridge import get_registry

        assert get_registry().get("ue5_validate_skeleton_bones") is not None

    def test_safe_and_read_only(self):
        from bionics_tools import ue5_autorig  # noqa: F401
        from core.bridge import SafetyTier, get_registry

        spec = get_registry().get("ue5_validate_skeleton_bones")
        assert spec.safety_tier == SafetyTier.SAFE
        assert spec.annotations.read_only is True
        assert spec.annotations.destructive is False

    def test_aliases(self):
        from bionics_tools import ue5_autorig  # noqa: F401
        from core.bridge import get_registry

        spec = get_registry().get("ue5_validate_skeleton_bones")
        assert "validate-skeleton-bones" in spec.aliases


# ============================================================================
# Validation + transport + fail-closed
# ============================================================================


class TestValidateTool:
    def test_empty_path_fails(self):
        from bionics_tools import ue5_autorig

        r = ue5_autorig.ue5_validate_skeleton_bones(skeletal_mesh_path="")
        assert r.ok is False
        assert "required" in r.error

    def test_non_content_path_fails(self):
        from bionics_tools import ue5_autorig

        r = ue5_autorig.ue5_validate_skeleton_bones(skeletal_mesh_path="C:/disk/SK_X")
        assert r.ok is False
        assert "content path" in r.error

    @staticmethod
    def _run(ue5_autorig, tmp_path, payload):
        def fake_fire(tool_name, args):
            (tmp_path / "validate_result.json").write_text(json.dumps(payload), encoding="utf-8")
            return ToolResult.success(content="deferred")

        with patch.object(ue5_autorig, "_resolve_validate_scratch_dir", return_value=tmp_path), \
             patch.object(ue5_autorig, "call_bridge_tool", side_effect=fake_fire), \
             patch("bionics_tools._ue5_native_exec.time.sleep"):
            return ue5_autorig.ue5_validate_skeleton_bones(skeletal_mesh_path="/Game/X/SK_SW_Hero")

    def test_humanoid_success(self, tmp_path):
        from bionics_tools import ue5_autorig

        payload = {
            "ok": True, "stage": "done",
            "bones": {"count": 23, "method": "SkeletalMeshComponent", "humanoid": True,
                      "mannequin_missing": []},
            "errors": [],
        }
        r = self._run(ue5_autorig, tmp_path, payload)
        assert r.ok is True
        assert r.data["humanoid"] is True
        assert r.data["bone_count"] == 23

    def test_fail_closed_non_humanoid(self, tmp_path):
        from bionics_tools import ue5_autorig

        payload = {
            "ok": False, "stage": "validate_bones",
            "bones": {"count": 7, "method": "SkeletalMeshComponent", "humanoid": False,
                      "mannequin_missing": ["root", "pelvis", "spine_01"]},
            "errors": ["missing 16/23 Mannequin core bones — not a valid humanoid (FAIL-CLOSED)"],
        }
        r = self._run(ue5_autorig, tmp_path, payload)
        assert r.ok is False
        assert r.data["humanoid"] is False
        assert "Mannequin core" in r.error

    def test_fail_closed_no_bones(self, tmp_path):
        from bionics_tools import ue5_autorig

        payload = {
            "ok": False, "stage": "validate_bones",
            "bones": {"count": 0, "method": None},
            "errors": ["bone extraction failed all 4 methods — FAIL-CLOSED (cannot validate)"],
        }
        r = self._run(ue5_autorig, tmp_path, payload)
        assert r.ok is False
        assert "bone extraction failed" in r.error

    def test_no_ikrig_side_effect_in_script(self):
        """The validate-only script must NOT build an IKRig (read-only contract)."""
        from bionics_tools import ue5_autorig

        assert "IKRigDefinitionFactory" not in ue5_autorig._VALIDATE_ONLY_SCRIPT
        assert "add_retarget_chain" not in ue5_autorig._VALIDATE_ONLY_SCRIPT
        assert "get_bones" in ue5_autorig._VALIDATE_ONLY_SCRIPT

    def test_bridge_unreachable_fails(self, tmp_path):
        from bionics_tools import ue5_autorig

        with patch.object(ue5_autorig, "_resolve_validate_scratch_dir", return_value=tmp_path), \
             patch.object(ue5_autorig, "call_bridge_tool",
                          return_value=ToolResult.failure("Bridge unreachable")), \
             patch("bionics_tools._ue5_native_exec.time.sleep"):
            r = ue5_autorig.ue5_validate_skeleton_bones(skeletal_mesh_path="/Game/X/SK_X")
        assert r.ok is False
        assert "could not queue" in r.error


# ============================================================================
# MVPDoctor ASSET check integration
# ============================================================================


class _FakeBridge:
    def __init__(self, connected=True):
        self.is_connected = connected


class TestDoctorBoneIntegrityCheck:
    def test_skips_when_bridge_none(self, tmp_path):
        from core.mvp_doctor import MVPDoctor, Severity

        (tmp_path / "Content").mkdir(parents=True, exist_ok=True)
        doctor = MVPDoctor(ue5_project_path=str(tmp_path), ue5_bridge=None)
        findings = doctor.check_skeleton_bone_integrity()
        assert len(findings) == 1
        assert findings[0].id == "BONE_INTEGRITY_NO_BRIDGE"
        assert findings[0].severity == Severity.INFO

    def test_skips_when_no_sk_assets(self, tmp_path):
        from core.mvp_doctor import MVPDoctor

        (tmp_path / "Content").mkdir(parents=True, exist_ok=True)
        doctor = MVPDoctor(ue5_project_path=str(tmp_path), ue5_bridge=_FakeBridge(connected=True))
        assert doctor.check_skeleton_bone_integrity() == []

    def test_reports_missing_bones(self, tmp_path):
        from core.mvp_doctor import MVPDoctor, Severity

        content = tmp_path / "Content"
        content.mkdir(parents=True, exist_ok=True)
        (content / "SK_SW_Hero.uasset").write_bytes(b"\x00")  # presence only; tool is mocked

        doctor = MVPDoctor(ue5_project_path=str(tmp_path), ue5_bridge=_FakeBridge(connected=True))

        def fake_validate(skeletal_mesh_path, timeout_s=30.0):
            return ToolResult(ok=False, content="bad", data={"mannequin_missing": ["root"]},
                              error="missing core bones")

        with patch("bionics_tools.ue5_autorig.ue5_validate_skeleton_bones", side_effect=fake_validate):
            findings = doctor.check_skeleton_bone_integrity()
        assert any(f.id.startswith("BONE_INTEGRITY_FAIL_") for f in findings)
        assert all(f.severity == Severity.HIGH for f in findings if f.id.startswith("BONE_INTEGRITY_FAIL_"))

    def test_passes_healthy_skeleton(self, tmp_path):
        from core.mvp_doctor import MVPDoctor

        content = tmp_path / "Content"
        content.mkdir(parents=True, exist_ok=True)
        (content / "SK_SW_Hero.uasset").write_bytes(b"\x00")

        doctor = MVPDoctor(ue5_project_path=str(tmp_path), ue5_bridge=_FakeBridge(connected=True))

        def fake_validate(skeletal_mesh_path, timeout_s=30.0):
            return ToolResult.success(content="ok", data={"humanoid": True})

        with patch("bionics_tools.ue5_autorig.ue5_validate_skeleton_bones", side_effect=fake_validate):
            findings = doctor.check_skeleton_bone_integrity()
        assert findings == []
