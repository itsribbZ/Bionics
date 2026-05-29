"""Tests for the autorig (validate + IKRig) native tool.

Without a live UE5 these verify: registration, IKRig-name derivation, input validation,
the deferred fire-and-poll transport (mocked bridge + simulated result file), and the
fail-closed gate BOTH ways (non-humanoid + unreadable bones). The front-to-back live
validate+rig is scripts/livefire_autorig.py (needs UE5 open) — the Rule #13 test.
"""

import json
from unittest.mock import patch

from core.bridge import ToolResult

# ============================================================================
# Registration + name derivation
# ============================================================================


class TestAutorigRegistration:
    def test_tool_registered(self):
        from bionics_tools import ue5_autorig  # noqa: F401 — import registers
        from core.bridge import get_registry

        assert get_registry().get("ue5_autorig_humanoid") is not None

    def test_category_and_safety(self):
        from bionics_tools import ue5_autorig  # noqa: F401
        from core.bridge import SafetyTier, get_registry

        spec = get_registry().get("ue5_autorig_humanoid")
        assert spec.category == "ue5_autorig"
        assert spec.safety_tier == SafetyTier.MODERATE

    def test_aliases(self):
        from bionics_tools import ue5_autorig  # noqa: F401
        from core.bridge import get_registry

        spec = get_registry().get("ue5_autorig_humanoid")
        assert "autorig-humanoid" in spec.aliases
        assert "autorig" in spec.aliases


class TestDeriveIkrigName:
    def test_sk_prefix_swapped(self):
        from bionics_tools import ue5_autorig

        assert ue5_autorig._derive_ikrig_name("/Game/Test/Skel/SK_X/SkeletalMeshes/SK_X") == "IKR_X"

    def test_plain_leaf_prefixed(self):
        from bionics_tools import ue5_autorig

        assert ue5_autorig._derive_ikrig_name("/Game/Foo/Mannequin") == "IKR_Mannequin"

    def test_trailing_slash_tolerated(self):
        from bionics_tools import ue5_autorig

        assert ue5_autorig._derive_ikrig_name("/Game/Test/SK_Hero/") == "IKR_Hero"


# ============================================================================
# Validate + rig — validation, transport, fail-closed
# ============================================================================


class TestAutorig:
    def test_empty_path_fails(self):
        from bionics_tools import ue5_autorig

        r = ue5_autorig.ue5_autorig_humanoid(skeletal_mesh_path="")
        assert r.ok is False
        assert "required" in r.error

    def test_non_content_path_fails(self):
        from bionics_tools import ue5_autorig

        r = ue5_autorig.ue5_autorig_humanoid(skeletal_mesh_path="C:/disk/SK_X")
        assert r.ok is False
        assert "content path" in r.error

    def test_success(self, tmp_path):
        from bionics_tools import ue5_autorig

        result_payload = {
            "ok": True,
            "stage": "done",
            "bones": {"count": 23, "method": "SkeletalMeshComponent", "humanoid": True, "mannequin_missing": []},
            "ikrig": {
                "path": "/Game/Test/Skel/IKR_X",
                "configured_count": 9,
                "verified_count": 9,
            },
            "errors": [],
        }

        def fake_fire(tool_name, args):
            (tmp_path / "rig_result.json").write_text(json.dumps(result_payload), encoding="utf-8")
            return ToolResult.success(content="deferred")

        with patch.object(ue5_autorig, "_resolve_scratch_dir", return_value=tmp_path), \
             patch.object(ue5_autorig, "call_bridge_tool", side_effect=fake_fire), \
             patch("bionics_tools._ue5_native_exec.time.sleep"):
            r = ue5_autorig.ue5_autorig_humanoid(
                skeletal_mesh_path="/Game/Test/Skel/SK_X/SkeletalMeshes/SK_X"
            )

        assert r.ok is True
        assert r.data["humanoid"] is True
        assert r.data["configured_count"] == 9
        assert r.data["ikrig_name"] == "IKR_X"

    def test_fail_closed_non_humanoid(self, tmp_path):
        """The canonical false-pass killer: a non-humanoid mesh must NOT get a rig."""
        from bionics_tools import ue5_autorig

        result_payload = {
            "ok": False,
            "stage": "validate_bones",
            "bones": {"count": 7, "method": "SkeletalMeshComponent", "humanoid": False,
                      "mannequin_missing": ["root", "pelvis", "spine_01"]},
            "ikrig": {},
            "errors": ["missing 16/23 Mannequin core bones ['root', 'pelvis'] — not riggable as humanoid (FAIL-CLOSED, no rig built)"],
        }

        def fake_fire(tool_name, args):
            (tmp_path / "rig_result.json").write_text(json.dumps(result_payload), encoding="utf-8")
            return ToolResult.success(content="deferred")

        with patch.object(ue5_autorig, "_resolve_scratch_dir", return_value=tmp_path), \
             patch.object(ue5_autorig, "call_bridge_tool", side_effect=fake_fire), \
             patch("bionics_tools._ue5_native_exec.time.sleep"):
            r = ue5_autorig.ue5_autorig_humanoid(
                skeletal_mesh_path="/Game/Test/Skel/SK_SW_Formahger/SkeletalMeshes/SK_SW_Formahger"
            )

        assert r.ok is False
        assert r.data["humanoid"] is False
        assert "Mannequin core" in r.error

    def test_fail_closed_no_bones(self, tmp_path):
        from bionics_tools import ue5_autorig

        result_payload = {
            "ok": False,
            "stage": "validate_bones",
            "bones": {"count": 0, "method": None},
            "ikrig": {},
            "errors": ["bone extraction failed all 4 methods — FAIL-CLOSED (cannot validate)"],
        }

        def fake_fire(tool_name, args):
            (tmp_path / "rig_result.json").write_text(json.dumps(result_payload), encoding="utf-8")
            return ToolResult.success(content="deferred")

        with patch.object(ue5_autorig, "_resolve_scratch_dir", return_value=tmp_path), \
             patch.object(ue5_autorig, "call_bridge_tool", side_effect=fake_fire), \
             patch("bionics_tools._ue5_native_exec.time.sleep"):
            r = ue5_autorig.ue5_autorig_humanoid(skeletal_mesh_path="/Game/X/SK_X")

        assert r.ok is False
        assert "bone extraction failed" in r.error

    def test_bridge_unreachable_fails(self, tmp_path):
        from bionics_tools import ue5_autorig

        with patch.object(ue5_autorig, "_resolve_scratch_dir", return_value=tmp_path), \
             patch.object(
                 ue5_autorig,
                 "call_bridge_tool",
                 return_value=ToolResult.failure("Bridge unreachable: connection refused"),
             ), \
             patch("bionics_tools._ue5_native_exec.time.sleep"):
            r = ue5_autorig.ue5_autorig_humanoid(skeletal_mesh_path="/Game/X/SK_X")

        assert r.ok is False
        assert "could not queue" in r.error

    def test_timeout_when_no_result(self, tmp_path):
        from bionics_tools import ue5_autorig

        seq = iter([0.0, 0.5] + [999.0] * 20)
        with patch.object(ue5_autorig, "_resolve_scratch_dir", return_value=tmp_path), \
             patch.object(ue5_autorig, "call_bridge_tool", return_value=ToolResult.success(content="deferred")), \
             patch("bionics_tools._ue5_native_exec.time.sleep"), \
             patch("bionics_tools._ue5_native_exec.time.monotonic", side_effect=lambda: next(seq)):
            r = ue5_autorig.ue5_autorig_humanoid(skeletal_mesh_path="/Game/X/SK_X", timeout_s=5.0)

        assert r.ok is False
        assert "Timed out" in r.error
