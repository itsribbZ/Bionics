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
                "configured_count": 7,
                "verified_count": 7,
                "unique_verified_count": 7,
                "expected_count": 7,
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
        assert r.data["configured_count"] == 7
        assert r.data["ikrig_name"] == "IKR_X"

    @staticmethod
    def _run_with_payload(ue5_autorig, tmp_path, result_payload):
        """Drive the tool with a simulated UE5-side result JSON (mocked deferred fire)."""
        def fake_fire(tool_name, args):
            (tmp_path / "rig_result.json").write_text(json.dumps(result_payload), encoding="utf-8")
            return ToolResult.success(content="deferred")

        with patch.object(ue5_autorig, "_resolve_scratch_dir", return_value=tmp_path), \
             patch.object(ue5_autorig, "call_bridge_tool", side_effect=fake_fire), \
             patch("bionics_tools._ue5_native_exec.time.sleep"):
            return ue5_autorig.ue5_autorig_humanoid(
                skeletal_mesh_path="/Game/Test/Skel/SK_X/SkeletalMeshes/SK_X"
            )

    def test_idempotent_clear_surfaced(self, tmp_path):
        """A re-run that cleared pre-existing chains plumbs cleared_count to the caller."""
        from bionics_tools import ue5_autorig

        payload = {
            "ok": True, "stage": "done",
            "bones": {"count": 23, "method": "SkeletalMeshComponent", "humanoid": True, "mannequin_missing": []},
            "ikrig": {"path": "/Game/Test/Skel/IKR_X", "configured_count": 7,
                      "verified_count": 7, "unique_verified_count": 7, "expected_count": 7, "cleared_count": 7},
            "errors": [],
        }
        r = self._run_with_payload(ue5_autorig, tmp_path, payload)
        assert r.ok is True
        assert r.data["cleared_count"] == 7          # the prior run's 7 chains were removed first
        assert r.data["unique_verified_count"] == 7  # rebuilt to exactly the canonical 7

    def test_dup_pollution_backstop_rejects_overcount(self, tmp_path):
        """Host backstop: even if the UE5-side ok flag regresses to True, an over-count (a 2x stack
        = 14 chains of the 7-canon) is rejected."""
        from bionics_tools import ue5_autorig

        payload = {
            "ok": True, "stage": "done",  # deliberately ok=True to prove the backstop is independent
            "bones": {"count": 23, "method": "SkeletalMeshComponent", "humanoid": True, "mannequin_missing": []},
            "ikrig": {"path": "/Game/Test/Skel/IKR_X", "configured_count": 7,
                      "verified_count": 14, "unique_verified_count": 7, "expected_count": 7, "cleared_count": 0},
            "errors": [],
        }
        r = self._run_with_payload(ue5_autorig, tmp_path, payload)
        assert r.ok is False
        assert "dup-pollution backstop" in r.error
        assert "14" in r.error

    def test_backstop_allows_zero_verified(self, tmp_path):
        """verified_count 0 = re-query genuinely failed (UE5-side trusts the adds) — NOT dup
        pollution, so the backstop must not fire."""
        from bionics_tools import ue5_autorig

        payload = {
            "ok": True, "stage": "done",
            "bones": {"count": 23, "method": "SkeletalMeshComponent", "humanoid": True, "mannequin_missing": []},
            "ikrig": {"path": "/Game/Test/Skel/IKR_X", "configured_count": 7,
                      "verified_count": 0, "unique_verified_count": 0, "expected_count": 7, "cleared_count": 0},
            "errors": [],
        }
        r = self._run_with_payload(ue5_autorig, tmp_path, payload)
        assert r.ok is True

    def test_dup_pollution_backstop_rejects_dup_at_expected(self, tmp_path):
        """Dup-at-expected: exactly 7 chain entries but only 6 unique names (one duplicated) — the
        count clause can't see it (7 == expected), so the uniqueness/mismatch clause must reject it."""
        from bionics_tools import ue5_autorig

        payload = {
            "ok": True, "stage": "done",  # ok=True to prove the backstop is independent of the UE5 gate
            "bones": {"count": 23, "method": "SkeletalMeshComponent", "humanoid": True, "mannequin_missing": []},
            "ikrig": {"path": "/Game/Test/Skel/IKR_X", "configured_count": 7,
                      "verified_count": 7, "unique_verified_count": 6, "expected_count": 7, "cleared_count": 0},
            "errors": [],
        }
        r = self._run_with_payload(ue5_autorig, tmp_path, payload)
        assert r.ok is False
        assert "dup-pollution backstop" in r.error
        assert "unique=6" in r.error

    def test_backstop_rejects_cleared_incomplete(self, tmp_path):
        """An incomplete pre-clear (stale chains survived the clear) must be rejected even if the
        UE5-side ok flag and counts look clean — never ship a rig stacked on un-cleared chains."""
        from bionics_tools import ue5_autorig

        payload = {
            "ok": True, "stage": "done",
            "bones": {"count": 23, "method": "SkeletalMeshComponent", "humanoid": True, "mannequin_missing": []},
            "ikrig": {"path": "/Game/Test/Skel/IKR_X", "configured_count": 7,
                      "verified_count": 0, "unique_verified_count": 0, "expected_count": 7,
                      "cleared_count": 3, "cleared_incomplete": True},
            "errors": ["pre-clear incomplete — 5 chain(s) survived removal"],
        }
        r = self._run_with_payload(ue5_autorig, tmp_path, payload)
        assert r.ok is False
        assert "cleared_incomplete=True" in r.error

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
