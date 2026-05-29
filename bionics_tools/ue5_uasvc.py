"""UE5 Asset Service (uasvc) — native-first skeletal import over the :8090 bridge.

The load-bearing piece of the idea->playable pivotal unlock (roadmap M3): import a
.glb/.gltf/.fbx that carries skin data and land a real SkeletalMesh + Skeleton +
PhysicsAsset. Ported VERBATIM from the live-proven seed
``blend-master/bridge/_ue5_import_skeletal_via_bionics.py`` (2026-05-28:
SK_SW_HumanoidTemplate.glb imported a 23-bone Mannequin skeleton; the Formahger sword
correctly landed as a non-humanoid). See feedback_ue5_skeletal_pipeline_proven_2026_05_28.

Transport: the C++ bridge runs Python on UE5's GAME THREAD, so
``execute_console_command`` with ``py exec(...)`` returns ``deferred=true`` immediately —
the real import result is written to a JSON file that this module polls (delete-then-poll,
so any appearance is a fresh result). This is the proven manual pattern productionized;
the one piece still pending live validation is the poll TIMING (see
``scripts/livefire_uasvc.py`` — the front-to-back test, runs only with UE5 open).

Fail-closed: import ok requires a real SkeletalMesh to land (``is_skeletal``). A StaticMesh
result means skin data was not detected — the skeletal rail did NOT close, so the tool
reports failure rather than a vacuous success. This is the canonical false-pass killer
that keeps a non-skeletal asset from riding into the rig stage.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Annotated

from bionics_tools.ue5_native import _configured_ue5_project_dir, call_bridge_tool
from core.bridge import SafetyTier, ToolResult, bionics_tool

# Canonical Interchange-FBX-flag regex — kept in lockstep with
# core.mvp_doctor.check_interchange_fbx_flag (the doctor-layer ASSET check). If the
# semantics drift, update both. glTF imports are unaffected by this flag.
_INTERCHANGE_FBX_RE = re.compile(
    r"^\s*Interchange\.FeatureFlags\.Import\.FBX\s*=\s*(\d+)", re.MULTILINE
)

_RESULT_MARKER = "BIONICS_SKEL_IMPORT_RESULT"

# UE5-side import script — proven VERBATIM from the 2026-05-28 seed, with only the two
# scratch paths parametrized via __PARAMS_PATH__ / __RESULT_PATH__ sentinels (the sole
# change from the live-proven original; runs inside UE5's interpreter via py exec).
_SKELETAL_IMPORT_SCRIPT = r'''
import json
import os
import traceback

import unreal

PARAMS_PATH = r"__PARAMS_PATH__"
RESULT_PATH = r"__RESULT_PATH__"

result = {"ok": False, "imported": [], "errors": [], "is_skeletal": False, "skeleton_path": None}

try:
    with open(PARAMS_PATH) as f:
        params = json.load(f)
    FILE_PATH = params["file_path"]
    DEST_PATH = params.get("dest_path", "/Game/Test/Skel")
    ASSET_NAME = params["asset_name"]
    result["target"] = {"file": FILE_PATH, "dest": DEST_PATH, "name": ASSET_NAME}

    if not os.path.exists(FILE_PATH):
        result["errors"].append(f"File not found: {FILE_PATH}")
    else:
        task = unreal.AssetImportTask()
        task.set_editor_property("filename", FILE_PATH)
        task.set_editor_property("destination_path", DEST_PATH)
        task.set_editor_property("destination_name", ASSET_NAME)
        task.set_editor_property("replace_existing", True)
        task.set_editor_property("automated", True)
        task.set_editor_property("save", True)

        ext = os.path.splitext(FILE_PATH)[1].lower()
        if ext == ".fbx":
            opts = unreal.FbxImportUI()
            opts.import_mesh = True
            opts.import_as_skeletal = True
            opts.import_materials = True
            opts.import_textures = True
            opts.import_animations = False
            opts.create_physics_asset = True
            opts.mesh_type_to_import = unreal.FBXImportType.FBXIT_SKELETAL_MESH
            skel_data = opts.skeletal_mesh_import_data
            skel_data.import_morph_targets = True
            skel_data.use_t0_as_ref_pose = True
            task.set_editor_property("options", opts)
            result["used_options"] = "FbxImportUI/skeletal"
        elif ext in (".glb", ".gltf") and hasattr(unreal, "GLTFImportOptions"):
            # The glTF importer auto-creates a SkeletalMesh when the asset carries skin
            # data (canonical skeletal path per roadmap; FBX Blender 5.1->5.7 is broken).
            opts = unreal.GLTFImportOptions()
            task.set_editor_property("options", opts)
            result["used_options"] = "GLTFImportOptions"
        else:
            result["used_options"] = "defaults"

        unreal.AssetToolsHelpers.get_asset_tools().import_asset_tasks([task])
        imported = list(task.get_editor_property("imported_object_paths") or [])
        result["imported"] = imported
        result["ok"] = len(imported) > 0

        # The pivotal assertion: did a real SkeletalMesh + Skeleton land?
        # (A StaticMesh result means the skin/armature was not imported.)
        for p in imported:
            try:
                obj = unreal.load_asset(p)
            except Exception as le:  # noqa: BLE001
                result["errors"].append(f"load_asset({p}) failed: {le}")
                continue
            if isinstance(obj, unreal.SkeletalMesh):
                result["is_skeletal"] = True
                skel = obj.get_editor_property("skeleton")
                if skel:
                    result["skeleton_path"] = skel.get_path_name()
            elif isinstance(obj, unreal.StaticMesh):
                result["errors"].append(
                    f"Imported {p} as StaticMesh, not SkeletalMesh — skin data not detected"
                )
except Exception as e:  # noqa: BLE001
    result["errors"].append(str(e))
    result["traceback"] = traceback.format_exc()

with open(RESULT_PATH, "w") as f:
    json.dump(result, f, indent=2)

unreal.log(
    f"BIONICS_SKEL_IMPORT_RESULT: ok={result['ok']} skeletal={result['is_skeletal']} "
    f"imported={len(result['imported'])} skeleton={result['skeleton_path']}"
)
'''


# ============================================================================
# Helpers
# ============================================================================


def _resolve_scratch_dir() -> Path | None:
    """Scratch dir for the import handshake files (params + script + result).

    Lives under the UE5 project's Saved/ so BOTH this process and the editor can
    read/write it; falls back to the system temp dir (same user) when the project
    path isn't configured. Returns None only if the dir cannot be created.
    """
    proj = _configured_ue5_project_dir()
    if proj:
        d = Path(proj) / "Saved" / "Bionics" / "uasvc"
    else:
        import tempfile

        d = Path(tempfile.gettempdir()) / "bionics_uasvc"
    try:
        d.mkdir(parents=True, exist_ok=True)
        return d
    except OSError:
        return None


def _preflight_fbx_interchange(project_dir: str) -> tuple[bool, str]:
    """FBX-only preflight: the legacy importer must be forced via
    ``Interchange.FeatureFlags.Import.FBX=0`` in DefaultEngine.ini, or skeletal FBX
    imports silently use the broken Interchange path and fail open. Mirrors
    core.mvp_doctor.check_interchange_fbx_flag (canonical). Returns (ok, message).
    glTF sources are unaffected and should skip this.
    """
    if not project_dir:
        return False, "paths.ue5_project not configured — cannot verify Interchange FBX flag"
    ini = Path(project_dir) / "Config" / "DefaultEngine.ini"
    try:
        content = ini.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False, f"DefaultEngine.ini not readable at {ini} — cannot verify Interchange FBX flag"
    m = _INTERCHANGE_FBX_RE.search(content)
    if m is None:
        return False, (
            "Interchange.FeatureFlags.Import.FBX is absent from DefaultEngine.ini — "
            "skeletal FBX imports will use the broken Interchange path (add `=0`)"
        )
    if m.group(1) != "0":
        return False, (
            f"Interchange.FeatureFlags.Import.FBX={m.group(1)}, must be 0 — "
            "skeletal FBX imports will fail open"
        )
    return True, "Interchange.FeatureFlags.Import.FBX=0 (legacy importer forced)"


def _fire_and_poll(
    script_path: Path,
    result_path: Path,
    timeout_s: float,
    poll_interval_s: float = 0.5,
) -> tuple[ToolResult | None, dict | None]:
    """Fire a UE5-side script over :8090 (deferred game-thread exec) and poll for its
    result JSON. Returns (error_result, None) on failure, or (None, parsed_dict) on success.

    The caller must delete result_path BEFORE staging, so any appearance here is a fresh
    result (no mtime races). The bridge returns deferred=true immediately, so the real
    result only exists once the game thread runs the script and writes result_path.
    """
    cmd = f"py exec(open(r'{script_path.as_posix()}').read())"
    fire = call_bridge_tool("execute_console_command", {"command": cmd})
    if not fire.ok:
        return ToolResult.failure(
            f"Bridge could not queue the import command: {fire.error}. "
            "Is UE5 + BionicsBridge running? (native :8090)"
        ), None

    deadline = time.monotonic() + max(1.0, float(timeout_s))
    last_err = ""
    while time.monotonic() < deadline:
        if result_path.exists():
            try:
                return None, json.loads(result_path.read_text(encoding="utf-8"))
            except (ValueError, OSError) as e:
                last_err = str(e)  # result file mid-write — keep polling
        time.sleep(poll_interval_s)

    msg = (
        f"Timed out after {float(timeout_s):.0f}s waiting for the deferred import result "
        f"({result_path.name}). The command was queued to UE5's game thread — check the "
        f"editor log for the {_RESULT_MARKER} marker."
    )
    if last_err:
        msg += f" Last read error: {last_err}"
    return ToolResult.failure(msg), None


# ============================================================================
# Tools
# ============================================================================


@bionics_tool(
    name="ue5_uasvc_preflight",
    category="ue5_uasvc",
    safety_tier=SafetyTier.SAFE,
    read_only=True,
    aliases=["uasvc-preflight", "skeletal-import-preflight"],
    title="uasvc Skeletal-Import Preflight",
)
def ue5_uasvc_preflight() -> ToolResult:
    """Static, offline check that the configured UE5 project is ready for skeletal FBX
    import: ``Interchange.FeatureFlags.Import.FBX=0`` in Config/DefaultEngine.ini.

    Read-only — reads no engine, just the project's ini. glTF (.glb/.gltf) imports do
    not depend on this flag, so a failing preflight only blocks .fbx sources.
    """
    project_dir = _configured_ue5_project_dir()
    ok, msg = _preflight_fbx_interchange(project_dir)
    data = {"interchange_fbx_ok": ok, "project_dir": project_dir, "detail": msg}
    if ok:
        return ToolResult.success(content=msg, data=data)
    return ToolResult(ok=False, content=f"Preflight: {msg}", data=data, error=msg)


@bionics_tool(
    name="ue5_uasvc_import_skeletal",
    category="ue5_uasvc",
    safety_tier=SafetyTier.MODERATE,
    aliases=["uasvc-import-skeletal", "import-skeletal"],
    title="Import Skeletal Mesh (uasvc, native :8090)",
)
def ue5_uasvc_import_skeletal(
    file_path: Annotated[str, "Absolute filesystem path to source .glb/.gltf/.fbx (must carry skin data)"],
    asset_name: Annotated[str, "Destination asset name, e.g. SK_SW_HumanoidTemplate"],
    dest_path: Annotated[str, "UE content path, e.g. /Game/Test/Skel"] = "/Game/Test/Skel",
    skip_preflight: Annotated[bool, "Skip the FBX Interchange-flag preflight (FBX source only)"] = False,
    timeout_s: Annotated[float, "Max seconds to wait for the deferred game-thread import"] = 60.0,
) -> ToolResult:
    """Import a skeletal mesh into UE5 over the native :8090 bridge — fail-closed on is_skeletal.

    Lands SkeletalMesh + Skeleton + PhysicsAsset from a .glb/.gltf (canonical) or .fbx
    (auto-preflighted for Interchange.FeatureFlags.Import.FBX=0 unless skip_preflight).
    Reports FAILURE if the source imports as a StaticMesh (skin data not detected) — the
    canonical false-pass killer that keeps a non-skeletal asset from riding into the rig stage.

    Deferred transport: the import runs on UE5's game thread; this stages a handshake
    script + params under <project>/Saved/Bionics/uasvc, fires it, then polls a result
    file for up to timeout_s. Ported from the live-proven 2026-05-28 seed.
    """
    file_path = (file_path or "").strip()
    asset_name = (asset_name or "").strip()
    if not file_path:
        return ToolResult.failure("file_path is required (absolute path to the source mesh).")
    if not asset_name:
        return ToolResult.failure("asset_name is required (e.g. SK_SW_HumanoidTemplate).")

    ext = Path(file_path).suffix.lower()
    if ext not in (".glb", ".gltf", ".fbx"):
        return ToolResult.failure(
            f"Unsupported source extension '{ext}'. Use .glb/.gltf (canonical) or .fbx."
        )

    project_dir = _configured_ue5_project_dir()

    # FBX-only preflight (fail-closed): the legacy importer must be forced.
    if ext == ".fbx" and not skip_preflight:
        ok, msg = _preflight_fbx_interchange(project_dir)
        if not ok:
            return ToolResult.failure(
                f"FBX preflight failed: {msg}. Pass skip_preflight=True to override "
                "(not recommended), or use a .glb/.gltf source."
            )

    scratch = _resolve_scratch_dir()
    if scratch is None:
        return ToolResult.failure("Could not create a writable scratch dir for the import handshake.")

    params_path = scratch / "import_params.json"
    script_path = scratch / "import_skeletal.py"
    result_path = scratch / "import_result.json"

    # Delete any stale result so the poll only ever sees a fresh write (no mtime races).
    try:
        result_path.unlink(missing_ok=True)
    except OSError:
        pass

    try:
        params_path.write_text(
            json.dumps({"file_path": file_path, "dest_path": dest_path, "asset_name": asset_name}),
            encoding="utf-8",
        )
        script = (
            _SKELETAL_IMPORT_SCRIPT
            .replace("__PARAMS_PATH__", params_path.as_posix())
            .replace("__RESULT_PATH__", result_path.as_posix())
        )
        script_path.write_text(script, encoding="utf-8")
    except OSError as e:
        return ToolResult.failure(f"Failed to stage import handshake files in {scratch}: {e}")

    err, data = _fire_and_poll(script_path, result_path, timeout_s)
    if err is not None:
        return err

    errors = data.get("errors", [])
    imported = data.get("imported", [])
    is_skeletal = bool(data.get("is_skeletal"))
    import_ok = bool(data.get("ok"))
    payload = {
        "is_skeletal": is_skeletal,
        "skeleton_path": data.get("skeleton_path"),
        "imported": imported,
        "used_options": data.get("used_options"),
        "errors": errors,
        "asset_name": asset_name,
        "dest_path": dest_path,
        "result_file": str(result_path),
    }

    # Fail-closed: a real SkeletalMesh must have landed.
    if import_ok and is_skeletal:
        return ToolResult.success(
            content=(
                f"Imported {asset_name} as SkeletalMesh ({len(imported)} object(s)); "
                f"skeleton={data.get('skeleton_path')}"
            ),
            data=payload,
        )

    reason = (
        "; ".join(errors)
        if errors
        else ("imported as non-skeletal (StaticMesh?)" if imported else "nothing imported")
    )
    return ToolResult(
        ok=False,
        content=f"Skeletal import of {asset_name} failed-closed: {reason}",
        data=payload,
        error=reason,
    )
