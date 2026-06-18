"""UE5 Retarget — native-first fail-closed batch animation retarget over :8090 (M5 Stage 2).

Punch-list #1 + #3 (roadmap-coverage scan 2026-05-30): the only existing retarget tool,
ue5_rigging.ue5_batch_retarget, is built on the DEAD UE5.7 API ``IKRetargetBatchOperationNameRule``
(abi_guard S7.M) and runs over the Remote-Control rail (HTTP 400 in 5.7). This is its
replacement: the UE5.7-correct ``IKRetargetBatchOperation.duplicate_and_retarget(...)`` over the
native :8090 bridge, ported VERBATIM from the live-proven recipe in the M5 Stage-2 scratch
(``duplicate_and_retarget(assets, src_mesh, tgt_mesh, rtg, search, replace, prefix, suffix,
include_referenced, overwrite)`` — positional, mesh-based, NO name-rule object).

Fail-closed (the proven gate set — each was live-exercised 2026-05-29):
  - retargeter asset doesn't load            -> FAIL
  - target SkeletalMesh can't be resolved    -> FAIL (from the retargeter's target rig preview
                                                mesh, or an explicit target_mesh_path override)
  - source SkeletalMesh can't be resolved    -> FAIL (source rig preview mesh -> Mannequin fallback)
  - zero AnimSequence assets in the source   -> FAIL (nothing to retarget)
  - duplicate_and_retarget produces 0 outputs-> FAIL (don't report success on an empty batch)

Best-effort observability (NOT a hard gate, because the chain-mapping query API is not yet
live-verified and a wrong symbol must not fail-close a valid retarget): the tool reports how
many target chains are mapped to a source chain, and ONLY hard-fails when that query SUCCEEDS
and the count is below ``min_mapped_chains``. If the query can't run, it records
``mapped_chains_verified=False`` and proceeds on the proven result>0 gate. Promote to a hard
gate once the chain-query API is confirmed live (livefire_retarget.py).

Root motion is governed by the retargeter ASSET's root settings (configured upstream at
retargeter creation), not at batch time — so there is intentionally no root-motion param here
(shipping one would be a no-op lie). Transport mirrors ue5_autorig / ue5_uasvc (deferred
``py exec`` over :8090 + delete-then-poll). Front-to-back live-fire: scripts/livefire_retarget.py.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

from bionics_tools._ue5_native_exec import fire_and_poll, resolve_scratch_dir
from bionics_tools.ue5_native import call_bridge_tool
from core.bridge import SafetyTier, ToolResult, bionics_tool

_RETARGET_MARKER = "BIONICS_RETARGET_RESULT"

# UE5-side batch-retarget script. Ported from the live-proven M5 Stage-2 recipe with params
# widened (RTG / source folder / dest / name-change / target+source overrides / min mapped
# chains) via a params JSON, and the two scratch paths parametrized via sentinels.
_BATCH_RETARGET_SCRIPT = r'''
import json
import traceback

import unreal

PARAMS_PATH = r"__PARAMS_PATH__"
RESULT_PATH = r"__RESULT_PATH__"
with open(PARAMS_PATH) as _pf:
    _p = json.load(_pf)
RTG = _p["retargeter_path"]
SRC_FOLDER = _p["source_folder"]
DEST_FOLDER = _p.get("dest_folder", "")
SEARCH = _p.get("search", "")
REPLACE = _p.get("replace", "")
PREFIX = _p.get("prefix", "")
SUFFIX = _p.get("suffix", "")
INCLUDE_REFERENCED = bool(_p.get("include_referenced", False))
OVERWRITE = bool(_p.get("overwrite", True))
TARGET_MESH_PATH = _p.get("target_mesh_path", "")
SOURCE_MESH_PATH = _p.get("source_mesh_path", "")
MANNEQUIN_FALLBACK = _p.get(
    "mannequin_fallback", "/Game/Characters/Mannequins/Meshes/SKM_Manny_Simple"
)
MIN_MAPPED = int(_p.get("min_mapped_chains", 0))

RES = {"ok": False, "stage": "init", "retargeted": [], "errors": [],
       "mapped_chains_verified": False, "mapped_chains": None}


def run():
    RES["stage"] = "load_rtg"
    rtg = unreal.load_asset(RTG)
    if not rtg:
        RES["errors"].append(f"could not load retargeter {RTG}")
        return

    RES["stage"] = "resolve_meshes"
    ctrl = unreal.IKRetargeterController.get_controller(rtg)
    src_rig = ctrl.get_ik_rig(unreal.RetargetSourceOrTarget.SOURCE) if ctrl else None
    tgt_rig = ctrl.get_ik_rig(unreal.RetargetSourceOrTarget.TARGET) if ctrl else None

    # Target mesh: explicit override, else the retargeter's target rig preview mesh.
    tgt_mesh = unreal.load_asset(TARGET_MESH_PATH) if TARGET_MESH_PATH else None
    if not tgt_mesh and tgt_rig:
        try:
            tgt_mesh = tgt_rig.get_preview_mesh()
        except Exception as e:  # noqa: BLE001
            RES["errors"].append("target preview mesh: " + str(e))
    if not tgt_mesh:
        RES["errors"].append("target SkeletalMesh could not be resolved (no override + no target preview mesh)")
        return

    # Source mesh: explicit override, else source rig preview mesh, else Mannequin fallback.
    src_mesh = unreal.load_asset(SOURCE_MESH_PATH) if SOURCE_MESH_PATH else None
    if not src_mesh and src_rig:
        try:
            src_mesh = src_rig.get_preview_mesh()
        except Exception as e:  # noqa: BLE001
            RES["errors"].append("source preview mesh: " + str(e))
    if not src_mesh:
        src_mesh = unreal.load_asset(MANNEQUIN_FALLBACK)
    if not src_mesh:
        RES["errors"].append("source SkeletalMesh None and Mannequin fallback failed")
        return

    # Best-effort mapped-chain observability (NOT a hard gate unless the query works AND is low).
    RES["stage"] = "check_chains"
    try:
        mappings = None
        for getter in ("get_chain_mappings", "get_retarget_chain_mappings"):
            fn = getattr(ctrl, getter, None)
            if fn:
                mappings = fn()
                break
        if mappings is not None:
            mapped = 0
            for m in mappings:
                src_name = getattr(m, "source_chain", None)
                if src_name is None and hasattr(m, "get_editor_property"):
                    try:
                        src_name = m.get_editor_property("source_chain")
                    except Exception:  # noqa: BLE001
                        src_name = None
                if src_name and str(src_name) not in ("None", "NAME_None", ""):
                    mapped += 1
            RES["mapped_chains"] = mapped
            RES["mapped_chains_verified"] = True
            if MIN_MAPPED and mapped < MIN_MAPPED:
                RES["errors"].append(
                    f"only {mapped}/{MIN_MAPPED} target chains mapped to a source chain "
                    "— retarget would T-pose unmapped chains (FAIL-CLOSED)"
                )
                return
    except Exception as e:  # noqa: BLE001 — query API unverified; degrade, don't fail-close
        RES["errors"].append("mapped-chain query unavailable (non-fatal): " + str(e))

    RES["stage"] = "collect_anims"
    ar = unreal.AssetRegistryHelpers.get_asset_registry()
    filt = unreal.ARFilter(
        package_paths=[SRC_FOLDER], recursive_paths=True, class_names=["AnimSequence"]
    )
    found = ar.get_assets(filt)
    assets = [a.get_asset() for a in found]
    RES["found_count"] = len(assets)
    if not assets:
        RES["errors"].append(f"no AnimSequence assets under {SRC_FOLDER}")
        return

    RES["stage"] = "retarget"
    try:
        new_assets = unreal.IKRetargetBatchOperation.duplicate_and_retarget(
            assets, src_mesh, tgt_mesh, rtg,
            SEARCH, REPLACE, PREFIX, SUFFIX,
            INCLUDE_REFERENCED, OVERWRITE,
        )
        RES["retargeted"] = [a.get_path_name() for a in (new_assets or [])][:60]
        RES["retargeted_count"] = len(new_assets or [])
        RES["ok"] = RES["retargeted_count"] > 0
        if not RES["ok"]:
            RES["errors"].append("duplicate_and_retarget produced 0 outputs (FAIL-CLOSED)")
        RES["stage"] = "done"
    except Exception as e:  # noqa: BLE001
        RES["errors"].append(f"duplicate_and_retarget raised: {e}")


try:
    run()
except Exception as e:  # noqa: BLE001
    RES["errors"].append(str(e))
    RES["traceback"] = traceback.format_exc()

with open(RESULT_PATH, "w") as f:
    json.dump(RES, f, indent=2)

unreal.log(
    f"BIONICS_RETARGET_RESULT: ok={RES['ok']} count={RES.get('retargeted_count')} "
    f"mapped={RES.get('mapped_chains')} verified={RES.get('mapped_chains_verified')}"
)
'''


def _resolve_retarget_scratch_dir() -> Path | None:
    """Scratch dir for the retarget handshake files — module-level seam so unit tests can
    patch ue5_retarget._resolve_retarget_scratch_dir."""
    return resolve_scratch_dir("retarget")


def _fire_and_poll_retarget(
    script_path: Path,
    result_path: Path,
    timeout_s: float,
    poll_interval_s: float = 0.5,
) -> tuple[ToolResult | None, dict | None]:
    """Fire the batch-retarget script over :8090 and poll its result JSON. ``call_bridge_tool``
    is passed through so tests that patch ue5_retarget.call_bridge_tool still intercept the fire."""
    return fire_and_poll(
        script_path, result_path, timeout_s,
        invoke=call_bridge_tool, noun="retarget", marker=_RETARGET_MARKER,
        poll_interval_s=poll_interval_s,
    )


@bionics_tool(
    name="ue5_batch_retarget_anims",
    category="ue5_retarget",
    safety_tier=SafetyTier.DESTRUCTIVE,
    destructive=True,
    aliases=["batch-retarget-anims", "retarget-anims"],
    title="Batch Retarget Animations (UE5.7 duplicate_and_retarget, native :8090)",
)
def ue5_batch_retarget_anims(
    retargeter_path: Annotated[str, "IKRetargeter asset path, e.g. /Game/Test/Skel/RTG_Mannequin_to_SW_HumanoidTemplate"],
    source_folder: Annotated[str, "Folder of source AnimSequences, e.g. /Game/Characters/Mannequins/Animations/Manny"],
    dest_folder: Annotated[str, "Output folder for retargeted anims (informational; UE writes alongside via suffix)"] = "",
    target_mesh_path: Annotated[str, "Optional explicit target SkeletalMesh (else from the retargeter's target rig)"] = "",
    source_mesh_path: Annotated[str, "Optional explicit source SkeletalMesh (else source rig preview -> Mannequin fallback)"] = "",
    search: Annotated[str, "Rename: substring to find in output names"] = "",
    replace: Annotated[str, "Rename: replacement substring"] = "",
    prefix: Annotated[str, "Output name prefix"] = "",
    suffix: Annotated[str, "Output name suffix, e.g. _SWHT"] = "_Retargeted",
    include_referenced: Annotated[bool, "Also retarget referenced assets"] = False,
    overwrite: Annotated[bool, "Overwrite existing output files"] = True,
    min_mapped_chains: Annotated[int, "If >0 AND the chain-mapping query works, fail-closed below this many mapped chains"] = 0,
    timeout_s: Annotated[float, "Max seconds for the deferred batch"] = 300.0,
) -> ToolResult:
    """Batch-retarget AnimSequences source->target via the UE5.7 IKRetargetBatchOperation,
    over the native :8090 bridge — the M5 Stage-2 replacement for the dead-API ue5_batch_retarget.

    Resolves source+target SkeletalMeshes from the retargeter (with an explicit override and a
    Mannequin source fallback), collects every AnimSequence under source_folder, and calls
    duplicate_and_retarget with inline name-change (search/replace/prefix/suffix). Fail-closed if
    the retargeter/meshes/anims can't be resolved or the batch produces zero outputs — never
    reports success on an empty retarget. Root motion follows the retargeter asset's root settings.
    """
    retargeter_path = (retargeter_path or "").strip()
    source_folder = (source_folder or "").strip()
    if not retargeter_path or not retargeter_path.startswith("/"):
        return ToolResult.failure(
            f"retargeter_path must be a UE content path starting with '/' (got '{retargeter_path}')."
        )
    if not source_folder or not source_folder.startswith("/"):
        return ToolResult.failure(
            f"source_folder must be a UE content path starting with '/' (got '{source_folder}')."
        )
    target_mesh_path = (target_mesh_path or "").strip()
    source_mesh_path = (source_mesh_path or "").strip()
    if target_mesh_path and not target_mesh_path.startswith("/"):
        return ToolResult.failure(f"target_mesh_path must start with '/' (got '{target_mesh_path}').")
    if source_mesh_path and not source_mesh_path.startswith("/"):
        return ToolResult.failure(f"source_mesh_path must start with '/' (got '{source_mesh_path}').")

    scratch = _resolve_retarget_scratch_dir()
    if scratch is None:
        return ToolResult.failure("Could not create a writable scratch dir for the retarget handshake.")

    params_path = scratch / "retarget_params.json"
    script_path = scratch / "batch_retarget.py"
    result_path = scratch / "retarget_result.json"

    try:
        result_path.unlink(missing_ok=True)  # so the poll only sees a fresh write
    except OSError:
        pass

    try:
        params_path.write_text(
            json.dumps({
                "retargeter_path": retargeter_path,
                "source_folder": source_folder,
                "dest_folder": dest_folder,
                "target_mesh_path": target_mesh_path,
                "source_mesh_path": source_mesh_path,
                "search": search,
                "replace": replace,
                "prefix": prefix,
                "suffix": suffix,
                "include_referenced": bool(include_referenced),
                "overwrite": bool(overwrite),
                "min_mapped_chains": int(min_mapped_chains),
            }),
            encoding="utf-8",
        )
        script = (
            _BATCH_RETARGET_SCRIPT
            .replace("__PARAMS_PATH__", params_path.as_posix())
            .replace("__RESULT_PATH__", result_path.as_posix())
        )
        script_path.write_text(script, encoding="utf-8")
    except OSError as e:
        return ToolResult.failure(f"Failed to stage retarget handshake files in {scratch}: {e}")

    err, data = _fire_and_poll_retarget(script_path, result_path, timeout_s)
    if err is not None:
        return err

    errors = data.get("errors", [])
    payload = {
        "retargeter_path": retargeter_path,
        "source_folder": source_folder,
        "found_count": data.get("found_count"),
        "retargeted_count": data.get("retargeted_count"),
        "retargeted": data.get("retargeted"),
        "mapped_chains": data.get("mapped_chains"),
        "mapped_chains_verified": data.get("mapped_chains_verified"),
        "stage": data.get("stage"),
        "errors": errors,
        "result_file": str(result_path),
    }

    if bool(data.get("ok")):
        return ToolResult.success(
            content=(
                f"Retargeted {data.get('retargeted_count')}/{data.get('found_count')} anims "
                f"via {retargeter_path.rsplit('/', 1)[-1]} (mapped_chains={data.get('mapped_chains')})."
            ),
            data=payload,
        )

    reason = "; ".join(errors) if errors else f"retarget failed at stage '{data.get('stage')}'"
    return ToolResult(
        ok=False,
        content=f"Batch retarget via {retargeter_path} failed-closed: {reason}",
        data=payload,
        error=reason,
    )
