"""Tests for the uasvc (UE5 Asset Service) native skeletal-import tool.

Without a live UE5 these verify: registration, the FBX Interchange preflight (a pure
ini read), input validation, the deferred fire-and-poll transport (mocked bridge +
simulated result file), and the fail-closed is_skeletal gate. The front-to-back live
import is scripts/livefire_uasvc.py (needs UE5 open) — that is the Rule #13 test.
"""

import json
from unittest.mock import patch

from core.bridge import ToolResult


def _write_default_engine_ini(project_dir, flag_line):
    cfg = project_dir / "Config"
    cfg.mkdir(parents=True, exist_ok=True)
    content = "[/Script/Engine.Engine]\n"
    if flag_line is not None:
        content += flag_line + "\n"
    (cfg / "DefaultEngine.ini").write_text(content, encoding="utf-8")


# ============================================================================
# Registration
# ============================================================================


class TestUasvcRegistration:
    def test_import_tool_registered(self):
        from bionics_tools import ue5_uasvc  # noqa: F401 — import registers
        from core.bridge import get_registry

        assert get_registry().get("ue5_uasvc_import_skeletal") is not None

    def test_import_tool_category_and_safety(self):
        from bionics_tools import ue5_uasvc  # noqa: F401
        from core.bridge import SafetyTier, get_registry

        spec = get_registry().get("ue5_uasvc_import_skeletal")
        assert spec.category == "ue5_uasvc"
        assert spec.safety_tier == SafetyTier.MODERATE

    def test_import_tool_aliases(self):
        from bionics_tools import ue5_uasvc  # noqa: F401
        from core.bridge import get_registry

        spec = get_registry().get("ue5_uasvc_import_skeletal")
        assert "uasvc-import-skeletal" in spec.aliases
        assert "import-skeletal" in spec.aliases

    def test_preflight_tool_registered_safe_readonly(self):
        from bionics_tools import ue5_uasvc  # noqa: F401
        from core.bridge import SafetyTier, get_registry

        spec = get_registry().get("ue5_uasvc_preflight")
        assert spec is not None
        assert spec.safety_tier == SafetyTier.SAFE
        assert spec.annotations.read_only is True


# ============================================================================
# Preflight (pure ini read — mirrors core.mvp_doctor.check_interchange_fbx_flag)
# ============================================================================


class TestPreflight:
    def test_flag_zero_passes(self, tmp_path):
        from bionics_tools import ue5_uasvc

        _write_default_engine_ini(tmp_path, "Interchange.FeatureFlags.Import.FBX=0")
        ok, msg = ue5_uasvc._preflight_fbx_interchange(str(tmp_path))
        assert ok is True
        assert "legacy importer" in msg

    def test_flag_one_fails(self, tmp_path):
        from bionics_tools import ue5_uasvc

        _write_default_engine_ini(tmp_path, "Interchange.FeatureFlags.Import.FBX=1")
        ok, msg = ue5_uasvc._preflight_fbx_interchange(str(tmp_path))
        assert ok is False
        assert "must be 0" in msg

    def test_flag_absent_fails(self, tmp_path):
        from bionics_tools import ue5_uasvc

        _write_default_engine_ini(tmp_path, None)
        ok, msg = ue5_uasvc._preflight_fbx_interchange(str(tmp_path))
        assert ok is False
        assert "absent" in msg

    def test_ini_missing_fails(self, tmp_path):
        from bionics_tools import ue5_uasvc

        ok, msg = ue5_uasvc._preflight_fbx_interchange(str(tmp_path))  # no Config/ at all
        assert ok is False
        assert "not readable" in msg

    def test_no_project_dir_fails(self):
        from bionics_tools import ue5_uasvc

        ok, msg = ue5_uasvc._preflight_fbx_interchange("")
        assert ok is False
        assert "not configured" in msg

    def test_preflight_tool_success(self, tmp_path):
        from bionics_tools import ue5_uasvc

        _write_default_engine_ini(tmp_path, "Interchange.FeatureFlags.Import.FBX=0")
        with patch.object(ue5_uasvc, "_configured_ue5_project_dir", return_value=str(tmp_path)):
            result = ue5_uasvc.ue5_uasvc_preflight()
        assert result.ok is True
        assert result.data["interchange_fbx_ok"] is True

    def test_preflight_tool_failure(self, tmp_path):
        from bionics_tools import ue5_uasvc

        _write_default_engine_ini(tmp_path, "Interchange.FeatureFlags.Import.FBX=1")
        with patch.object(ue5_uasvc, "_configured_ue5_project_dir", return_value=str(tmp_path)):
            result = ue5_uasvc.ue5_uasvc_preflight()
        assert result.ok is False
        assert result.data["interchange_fbx_ok"] is False


# ============================================================================
# Import — validation, transport, fail-closed gate
# ============================================================================


class TestImportSkeletal:
    def test_empty_file_path_fails(self):
        from bionics_tools import ue5_uasvc

        r = ue5_uasvc.ue5_uasvc_import_skeletal(file_path="", asset_name="SK_X")
        assert r.ok is False
        assert "file_path" in r.error

    def test_empty_asset_name_fails(self):
        from bionics_tools import ue5_uasvc

        r = ue5_uasvc.ue5_uasvc_import_skeletal(file_path="C:/x/a.glb", asset_name="")
        assert r.ok is False
        assert "asset_name" in r.error

    def test_bad_extension_fails(self):
        from bionics_tools import ue5_uasvc

        r = ue5_uasvc.ue5_uasvc_import_skeletal(file_path="C:/x/a.obj", asset_name="SK_X")
        assert r.ok is False
        assert "Unsupported" in r.error

    def test_gltf_import_success(self, tmp_path):
        from bionics_tools import ue5_uasvc

        result_payload = {
            "ok": True,
            "is_skeletal": True,
            "imported": ["/Game/Test/Skel/SK_X/SkeletalMeshes/SK_X"],
            "skeleton_path": "/Game/Test/Skel/SK_X/Skeleton",
            "errors": [],
            "used_options": "GLTFImportOptions",
        }

        def fake_fire(tool_name, args):
            (tmp_path / "import_result.json").write_text(json.dumps(result_payload), encoding="utf-8")
            return ToolResult.success(content="deferred", data={"deferred": True})

        with patch.object(ue5_uasvc, "_resolve_scratch_dir", return_value=tmp_path), \
             patch.object(ue5_uasvc, "call_bridge_tool", side_effect=fake_fire), \
             patch("bionics_tools._ue5_native_exec.time.sleep"):
            r = ue5_uasvc.ue5_uasvc_import_skeletal(file_path="C:/x/SK_X.glb", asset_name="SK_X")

        assert r.ok is True
        assert r.data["is_skeletal"] is True
        assert r.data["skeleton_path"] == "/Game/Test/Skel/SK_X/Skeleton"

    def test_fail_closed_on_static_mesh(self, tmp_path):
        from bionics_tools import ue5_uasvc

        result_payload = {
            "ok": True,
            "is_skeletal": False,  # imported, but NOT a SkeletalMesh
            "imported": ["/Game/Test/Skel/SK_X/SK_X"],
            "skeleton_path": None,
            "errors": ["Imported /Game/Test/Skel/SK_X/SK_X as StaticMesh, not SkeletalMesh — skin data not detected"],
            "used_options": "GLTFImportOptions",
        }

        def fake_fire(tool_name, args):
            (tmp_path / "import_result.json").write_text(json.dumps(result_payload), encoding="utf-8")
            return ToolResult.success(content="deferred")

        with patch.object(ue5_uasvc, "_resolve_scratch_dir", return_value=tmp_path), \
             patch.object(ue5_uasvc, "call_bridge_tool", side_effect=fake_fire), \
             patch("bionics_tools._ue5_native_exec.time.sleep"):
            r = ue5_uasvc.ue5_uasvc_import_skeletal(file_path="C:/x/SK_X.glb", asset_name="SK_X")

        assert r.ok is False
        assert r.data["is_skeletal"] is False
        assert "StaticMesh" in r.error

    def test_bridge_unreachable_fails(self, tmp_path):
        from bionics_tools import ue5_uasvc

        with patch.object(ue5_uasvc, "_resolve_scratch_dir", return_value=tmp_path), \
             patch.object(
                 ue5_uasvc,
                 "call_bridge_tool",
                 return_value=ToolResult.failure("Bridge unreachable: connection refused"),
             ), \
             patch("bionics_tools._ue5_native_exec.time.sleep"):
            r = ue5_uasvc.ue5_uasvc_import_skeletal(file_path="C:/x/SK_X.glb", asset_name="SK_X")

        assert r.ok is False
        assert "could not queue" in r.error

    def test_timeout_when_no_result(self, tmp_path):
        from bionics_tools import ue5_uasvc

        # Deadline computed from the 1st monotonic call; subsequent calls jump past it.
        seq = iter([0.0, 0.5] + [999.0] * 20)
        with patch.object(ue5_uasvc, "_resolve_scratch_dir", return_value=tmp_path), \
             patch.object(ue5_uasvc, "call_bridge_tool", return_value=ToolResult.success(content="deferred")), \
             patch("bionics_tools._ue5_native_exec.time.sleep"), \
             patch("bionics_tools._ue5_native_exec.time.monotonic", side_effect=lambda: next(seq)):
            r = ue5_uasvc.ue5_uasvc_import_skeletal(
                file_path="C:/x/SK_X.glb", asset_name="SK_X", timeout_s=5.0
            )

        assert r.ok is False
        assert "Timed out" in r.error

    def test_fbx_preflight_blocks_when_flag_wrong(self, tmp_path):
        """An .fbx source with a bad Interchange flag fails BEFORE any bridge call."""
        from bionics_tools import ue5_uasvc

        _write_default_engine_ini(tmp_path, "Interchange.FeatureFlags.Import.FBX=1")
        with patch.object(ue5_uasvc, "_configured_ue5_project_dir", return_value=str(tmp_path)), \
             patch.object(ue5_uasvc, "call_bridge_tool") as mock_fire:
            r = ue5_uasvc.ue5_uasvc_import_skeletal(file_path="C:/x/SK_X.fbx", asset_name="SK_X")

        assert r.ok is False
        assert "FBX preflight failed" in r.error
        mock_fire.assert_not_called()

    def test_fbx_skip_preflight_proceeds(self, tmp_path):
        from bionics_tools import ue5_uasvc

        _write_default_engine_ini(tmp_path, "Interchange.FeatureFlags.Import.FBX=1")  # bad, but skipped
        result_payload = {
            "ok": True,
            "is_skeletal": True,
            "imported": ["/Game/x/SK_X"],
            "skeleton_path": "/Game/x/Skel",
            "errors": [],
        }

        def fake_fire(tool_name, args):
            (tmp_path / "import_result.json").write_text(json.dumps(result_payload), encoding="utf-8")
            return ToolResult.success(content="deferred")

        with patch.object(ue5_uasvc, "_configured_ue5_project_dir", return_value=str(tmp_path)), \
             patch.object(ue5_uasvc, "_resolve_scratch_dir", return_value=tmp_path), \
             patch.object(ue5_uasvc, "call_bridge_tool", side_effect=fake_fire), \
             patch("bionics_tools._ue5_native_exec.time.sleep"):
            r = ue5_uasvc.ue5_uasvc_import_skeletal(
                file_path="C:/x/SK_X.fbx", asset_name="SK_X", skip_preflight=True
            )

        assert r.ok is True
