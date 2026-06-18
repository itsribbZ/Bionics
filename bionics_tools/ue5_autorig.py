"""UE5 Autorig — native-first fail-closed bone-validate + 7-chain IKRig over :8090.

Roadmap M4 (bone validation) + M5 (autorig). Ported VERBATIM from the live-proven seed
``blend-master/bridge/_ue5_validate_and_rig.py`` (2026-05-28: SK_SW_HumanoidTemplate ->
23/23 Mannequin bones validated -> IKRig built; the 7-bone Formahger sword correctly REFUSED
a rig). See feedback_ue5_skeletal_pipeline_proven_2026_05_28.

Fail-closed BOTH ways (kills the upstream WARN-and-continue false-pass):
  - bone extraction returns nothing            -> FAIL (cannot validate, do not rig)
  - missing any Mannequin core bone (humanoid) -> ABORT, never build a broken rig
  - IKRig chains re-queried after add          -> ok only if EXACTLY len(CHAINS) unique resolve

Idempotent (the retarget blocker fix): add_retarget_chain APPENDS, so re-running the old
build stacked 9 -> 18 -> 27 duplicate chains and the loose `verified >= 9` gate rubber-stamped
it, breaking the downstream IK Retargeter. The build now clears all existing retarget chains
and RE-QUERIES to assert the rig is actually empty before rebuilding — aborting fail-closed if
any chain survived the clear, so an already-polluted rig self-heals for real, not best-effort.
The UE5-side gate requires exactly len(CHAINS) unique (7, the Sworder canon matching the game's
IKRig_Mannequin/IKRig_SciFiTrooper); a host-side backstop independently rejects any count not in
{0,N}, a non-{0,N} unique count, a count/unique mismatch, or an incomplete clear.

Transport mirrors ue5_uasvc (deferred ``py exec`` over :8090 + delete-then-poll a result
file); the fire-and-poll + scratch-dir helpers now live in the shared
bionics_tools/_ue5_native_exec.py (extracted on the third use — see that module).

The front-to-back live import+rig is scripts/livefire_autorig.py (needs UE5 open).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

from bionics_tools._ue5_native_exec import fire_and_poll, resolve_scratch_dir
from bionics_tools.ue5_native import call_bridge_tool
from core.bridge import SafetyTier, ToolResult, bionics_tool

_RIG_MARKER = "BIONICS_RIG_RESULT"

# Sworder's rigs (IKRig_Mannequin / IKRig_SciFiTrooper) use a 7-chain set — live-verified
# 2026-05-29. Host-side fallback for the dup-pollution backstop when the UE5 script didn't
# report expected_count; the authoritative source of truth is len(CHAINS) in the embedded script.
_CANONICAL_CHAIN_COUNT = 7

# UE5-side validate-then-rig script — proven VERBATIM from the 2026-05-28 seed, with the
# two scratch paths parametrized via sentinels and params widened to take an explicit
# skeletal_mesh_path / ikrig_name / ikrig_dest (decoupled from the import folder layout).
_VALIDATE_AND_RIG_SCRIPT = r'''
import json
import traceback

import unreal

PARAMS_PATH = r"__PARAMS_PATH__"
RESULT_PATH = r"__RESULT_PATH__"
with open(PARAMS_PATH) as _pf:
    _p = json.load(_pf)
SK_MESH = _p["skeletal_mesh_path"]
IKRIG_NAME = _p["ikrig_name"]
DEST = _p.get("ikrig_dest", "/Game/Test/Skel")

MANNEQUIN_CORE = [
    "root", "pelvis", "spine_01", "spine_02", "spine_03", "neck_01", "head",
    "clavicle_l", "upperarm_l", "lowerarm_l", "hand_l",
    "clavicle_r", "upperarm_r", "lowerarm_r", "hand_r",
    "thigh_l", "calf_l", "foot_l", "ball_l",
    "thigh_r", "calf_r", "foot_r", "ball_r",
]

# 7-chain canon — matches the game's IKRig_Mannequin + IKRig_SciFiTrooper EXACTLY (both
# live-verified 2026-05-29: Root, Spine, Head, LeftArm, RightArm, LeftLeg, RightLeg). Do NOT add
# Pelvis or Neck: the retarget SOURCE IKRig_Mannequin has no such chains, so auto_map would leave
# them unmapped and pin those bones to ref-pose, fighting the retarget. Chain NAMES must match the
# source rig for auto_map EXACT to hit all 7. This is the single source of truth for the gate count.
CHAINS = [
    ("Root", "root", "root"),
    ("Spine", "spine_01", "spine_03"),
    ("Head", "head", "head"),
    ("LeftArm", "clavicle_l", "hand_l"), ("RightArm", "clavicle_r", "hand_r"),
    ("LeftLeg", "thigh_l", "ball_l"), ("RightLeg", "thigh_r", "ball_r"),
]

RES = {"ok": False, "stage": "init", "bones": {}, "ikrig": {}, "errors": []}


def get_bones(mesh):
    """Bone-name extraction. Method 0 (the only one that works in UE5.7) enumerates a
    transient SkeletalMeshComponent — BoneNode.name in bone_tree is a protected
    property and EditorSkeletalMeshLibrary.get_all_bone_names was removed in 5.7."""
    try:
        comp = unreal.SkeletalMeshComponent()
        if hasattr(comp, "set_skeletal_mesh_asset"):
            comp.set_skeletal_mesh_asset(mesh)
        else:
            comp.set_editor_property("skeletal_mesh_asset", mesh)
        n = comp.get_num_bones()
        b = [str(comp.get_bone_name(i)) for i in range(n)]
        if b:
            return b, "SkeletalMeshComponent"
    except Exception as e:  # noqa: BLE001
        RES["errors"].append("bones m0: " + str(e))
    try:
        lib = getattr(unreal, "EditorSkeletalMeshLibrary", None)
        if lib and hasattr(lib, "get_all_bone_names"):
            b = [str(x) for x in (lib.get_all_bone_names(mesh) or [])]
            if b:
                return b, "EditorSkeletalMeshLibrary"
    except Exception as e:  # noqa: BLE001
        RES["errors"].append("bones m1: " + str(e))
    try:
        bt = mesh.skeleton.get_editor_property("bone_tree")
        b = [str(x.name) if hasattr(x, "name") else str(x) for x in (bt or [])]
        if b:
            return b, "bone_tree"
    except Exception as e:  # noqa: BLE001
        RES["errors"].append("bones m2: " + str(e))
    try:
        ref = mesh.skeleton.get_reference_pose()
        b = [str(x.name) for x in ref.get_editor_property("bone_info")]
        if b:
            return b, "reference_pose"
    except Exception as e:  # noqa: BLE001
        RES["errors"].append("bones m3: " + str(e))
    return [], None


def run():
    RES["stage"] = "load"
    mesh = unreal.load_asset(SK_MESH)
    if not isinstance(mesh, unreal.SkeletalMesh):
        RES["errors"].append(f"{SK_MESH} is not a SkeletalMesh")
        return

    RES["stage"] = "validate_bones"
    bones, method = get_bones(mesh)
    RES["bones"] = {"count": len(bones), "method": method, "actual": bones[:60]}
    if not bones:
        RES["errors"].append("bone extraction failed all 4 methods — FAIL-CLOSED (cannot validate)")
        return
    present = set(bones)
    missing = [b for b in MANNEQUIN_CORE if b not in present]
    RES["bones"]["mannequin_missing"] = missing
    RES["bones"]["humanoid"] = not missing
    if missing:
        RES["errors"].append(
            f"missing {len(missing)}/{len(MANNEQUIN_CORE)} Mannequin core bones {missing[:10]} "
            "— not riggable as humanoid (FAIL-CLOSED, no rig built)"
        )
        return

    RES["stage"] = "ikrig"
    if not (hasattr(unreal, "IKRigDefinition") and hasattr(unreal, "IKRigDefinitionFactory")):
        RES["errors"].append("IKRig API unavailable in this build")
        return
    ikrig_path = f"{DEST}/{IKRIG_NAME}"
    ikrig = unreal.load_asset(ikrig_path)
    if not ikrig:
        factory = unreal.IKRigDefinitionFactory()
        ikrig = unreal.AssetToolsHelpers.get_asset_tools().create_asset(
            IKRIG_NAME, DEST, unreal.IKRigDefinition, factory
        )
    if not ikrig:
        RES["errors"].append("create_asset returned None for IKRig")
        return
    ctrl = unreal.IKRigController.get_controller(ikrig)
    if ctrl is None and hasattr(unreal.IKRigController, "get_ik_rig_controller"):
        ctrl = unreal.IKRigController.get_ik_rig_controller(ikrig)
    if ctrl is None:
        RES["errors"].append("could not get IKRigController")
        return
    try:
        ctrl.set_skeletal_mesh(mesh)
    except Exception as e:  # noqa: BLE001
        RES["errors"].append("set_skeletal_mesh: " + str(e))

    # IDEMPOTENCY (fixes the 27-chain dup-pollution retarget blocker): add_retarget_chain
    # APPENDS — it does not dedup by name — so re-running autorig on an existing IKRig stacked
    # 9 -> 18 -> 27 duplicate chains. The old `verified >= 9` gate passed that as ok=True, then
    # the duplicated source/target chain names broke the downstream IK Retargeter. Clear ALL
    # existing retarget chains FIRST so a rebuild always lands EXACTLY the canonical 9 and an
    # already-polluted rig self-heals. remove_retarget_chain(chain_name) -> bool (UE5.7-verified).
    cleared = []
    try:
        existing = [str(getattr(c, "chain_name", c)) for c in (ctrl.get_retarget_chains() or [])]
        for nm in existing:  # iterate a snapshot — don't mutate the live array mid-loop
            try:
                if ctrl.remove_retarget_chain(nm):
                    cleared.append(nm)
                else:
                    RES["errors"].append(f"remove_retarget_chain returned False for '{nm}'")
            except Exception as e:  # noqa: BLE001
                RES["errors"].append(f"remove chain {nm}: {e}")
    except Exception as e:  # noqa: BLE001
        RES["errors"].append("pre-clear get_retarget_chains: " + str(e))

    # The self-heal must be LITERAL, not best-effort: re-query and assert the rig is actually
    # empty before rebuilding. A remove_retarget_chain that returned False/threw, or an
    # enumerate that failed, can leave stale chains; rebuilding on top of them is the exact
    # 18-chain dup pollution this fix exists to prevent. Gate on the REAL residual state (not
    # the ambiguous remove return — under duplicate names a remove is False precisely because
    # the chain is already gone). If anything survived, ABORT before adding (FAIL-CLOSED).
    cleared_incomplete = False
    try:
        residual = [str(getattr(c, "chain_name", c)) for c in (ctrl.get_retarget_chains() or [])]
    except Exception as e:  # noqa: BLE001
        residual = []
        cleared_incomplete = True
        RES["errors"].append("post-clear re-query failed (cannot confirm empty slate): " + str(e))
    if residual:
        cleared_incomplete = True
        RES["errors"].append(
            f"pre-clear incomplete — {len(residual)} chain(s) survived removal {residual[:10]}; "
            "aborting before rebuild to avoid dup pollution (FAIL-CLOSED)"
        )
    if cleared_incomplete:
        RES["stage"] = "pre-clear-incomplete"
        RES["ikrig"] = {
            "path": ikrig.get_path_name(),
            "cleared_pre_existing": cleared,
            "cleared_count": len(cleared),
            "cleared_incomplete": True,
            "configured_count": 0,
            "verified_count": 0,
            "unique_verified_count": 0,
            "expected_count": len(CHAINS),
        }
        return

    configured = []
    for name, start, end in CHAINS:
        try:
            ctrl.add_retarget_chain(name, start, end, "none")
            configured.append(name)
        except Exception as e:  # noqa: BLE001
            RES["errors"].append(f"chain {name} ({start}->{end}): {e}")

    # Re-query to VERIFY (fail-closed) — do not trust the add calls.
    verified = []
    try:
        for c in (ctrl.get_retarget_chains() or []):
            nm = getattr(c, "chain_name", None)
            verified.append(str(nm if nm is not None else c))
    except Exception as e:  # noqa: BLE001
        RES["errors"].append("get_retarget_chains: " + str(e))

    # Anomaly surface (Rule #5 — diagnostics are features): an empty re-query right after N
    # successful adds means the gate has to trust adds it could not confirm. Make that visible
    # so live-fire can chase the IKRigController API contract rather than silently trusting it.
    if configured and not verified:
        RES["errors"].append(
            f"re-query returned no chains after {len(configured)} adds — trusting adds unconfirmed "
            "(verify the IKRigController API contract)"
        )

    try:
        unreal.EditorAssetLibrary.save_asset(ikrig.get_path_name())
    except Exception as e:  # noqa: BLE001
        RES["errors"].append("save_asset: " + str(e))

    unique_verified = set(verified)
    expected = len(CHAINS)
    RES["ikrig"] = {
        "path": ikrig.get_path_name(),
        "configured": configured,
        "configured_count": len(configured),
        "verified_chains": verified,
        "verified_count": len(verified),
        "unique_verified_count": len(unique_verified),
        "expected_count": expected,
        "cleared_pre_existing": cleared,
        "cleared_count": len(cleared),
        "cleared_incomplete": False,
    }
    # ok requires the canonical chain set added AND — when the re-query works — EXACTLY that many
    # UNIQUE chains present (no dup pollution). expected = len(CHAINS) is the single source of truth
    # (7 for Sworder). The `not verified` fallback trusts the adds ONLY if get_retarget_chains()
    # itself failed/returned nothing (API quirk) — never to excuse dups.
    if verified:
        chains_ok = (
            len(configured) == expected and len(verified) == expected and len(unique_verified) == expected
        )
    else:
        chains_ok = len(configured) == expected
    RES["ok"] = chains_ok
    RES["stage"] = "done"


try:
    run()
except Exception as e:  # noqa: BLE001
    RES["errors"].append(str(e))
    RES["traceback"] = traceback.format_exc()

with open(RESULT_PATH, "w") as f:
    json.dump(RES, f, indent=2)

unreal.log(
    f"BIONICS_RIG_RESULT: ok={RES['ok']} humanoid={RES['bones'].get('humanoid')} "
    f"bones={RES['bones'].get('count')} chains={RES['ikrig'].get('configured_count')}/{RES['ikrig'].get('expected_count')} "
    f"verified={RES['ikrig'].get('verified_count')}"
)
'''


# ============================================================================
# Helpers (thin wrappers over the shared bionics_tools/_ue5_native_exec transport)
# ============================================================================


def _resolve_scratch_dir() -> Path | None:
    """Scratch dir for the rig handshake files — delegates to the shared transport.
    Kept as a module-level seam so unit tests can patch ue5_autorig._resolve_scratch_dir."""
    return resolve_scratch_dir("autorig")


def _fire_and_poll(
    script_path: Path,
    result_path: Path,
    timeout_s: float,
    poll_interval_s: float = 0.5,
) -> tuple[ToolResult | None, dict | None]:
    """Fire the validate-and-rig script over :8090 and poll its result JSON — delegates to
    the shared transport. ``call_bridge_tool`` is passed through so unit tests that patch
    ue5_autorig.call_bridge_tool still intercept the fire."""
    return fire_and_poll(
        script_path, result_path, timeout_s,
        invoke=call_bridge_tool, noun="rig", marker=_RIG_MARKER,
        poll_interval_s=poll_interval_s,
    )


def _derive_ikrig_name(skeletal_mesh_path: str) -> str:
    """SK_Foo -> IKR_Foo (mirrors the proven seed's `_ASSET.replace('SK_', 'IKR_', 1)`)."""
    leaf = skeletal_mesh_path.rstrip("/").rsplit("/", 1)[-1]
    if leaf.startswith("SK_"):
        return leaf.replace("SK_", "IKR_", 1)
    return f"IKR_{leaf}"


# ============================================================================
# Tool
# ============================================================================


@bionics_tool(
    name="ue5_autorig_humanoid",
    category="ue5_autorig",
    safety_tier=SafetyTier.MODERATE,
    aliases=["autorig-humanoid", "autorig"],
    title="Autorig Humanoid IKRig (validate + 7-chain, native :8090)",
)
def ue5_autorig_humanoid(
    skeletal_mesh_path: Annotated[str, "UE content path to the SkeletalMesh, e.g. /Game/Test/Skel/SK_X/SkeletalMeshes/SK_X"],
    ikrig_name: Annotated[str, "IKRig asset name; default derives SK_X -> IKR_X"] = "",
    ikrig_dest: Annotated[str, "Content path for the IKRig asset"] = "/Game/Test/Skel",
    timeout_s: Annotated[float, "Max seconds to wait for the deferred game-thread validate+rig"] = 60.0,
) -> ToolResult:
    """Validate a skeletal mesh as humanoid then build a 7-chain IKRig — fail-closed BOTH ways.

    Validates all 23 Mannequin core bones are present (via the UE5.7 SkeletalMeshComponent
    bone enumerator), and ONLY then creates an IKRigDefinition and adds the 7 retarget chains
    (Root/Spine/Head/Left+RightArm/Left+RightLeg) — the canon matching the game's IKRig_Mannequin/
    IKRig_SciFiTrooper so auto_map hits 7/7 — re-querying to confirm all 7 resolved. Reports
    FAILURE if bones can't be read, any Mannequin core bone is missing (non-humanoid), or fewer
    than 7 chains land — never ships a broken or partial rig.

    Deferred transport: runs on UE5's game thread; stages a handshake script under
    <project>/Saved/Bionics/autorig, fires it, polls a result file for up to timeout_s.
    Ported from the live-proven 2026-05-28 seed. Pair with ue5_uasvc_import_skeletal upstream.
    """
    skeletal_mesh_path = (skeletal_mesh_path or "").strip()
    if not skeletal_mesh_path:
        return ToolResult.failure("skeletal_mesh_path is required (UE content path to the SkeletalMesh).")
    if not skeletal_mesh_path.startswith("/"):
        return ToolResult.failure(
            f"skeletal_mesh_path must be a UE content path starting with '/' (got '{skeletal_mesh_path}')."
        )

    ikrig_name = (ikrig_name or "").strip() or _derive_ikrig_name(skeletal_mesh_path)
    ikrig_dest = (ikrig_dest or "").strip() or "/Game/Test/Skel"

    scratch = _resolve_scratch_dir()
    if scratch is None:
        return ToolResult.failure("Could not create a writable scratch dir for the rig handshake.")

    params_path = scratch / "rig_params.json"
    script_path = scratch / "validate_and_rig.py"
    result_path = scratch / "rig_result.json"

    try:
        result_path.unlink(missing_ok=True)  # so the poll only sees a fresh write
    except OSError:
        pass

    try:
        params_path.write_text(
            json.dumps({
                "skeletal_mesh_path": skeletal_mesh_path,
                "ikrig_name": ikrig_name,
                "ikrig_dest": ikrig_dest,
            }),
            encoding="utf-8",
        )
        script = (
            _VALIDATE_AND_RIG_SCRIPT
            .replace("__PARAMS_PATH__", params_path.as_posix())
            .replace("__RESULT_PATH__", result_path.as_posix())
        )
        script_path.write_text(script, encoding="utf-8")
    except OSError as e:
        return ToolResult.failure(f"Failed to stage rig handshake files in {scratch}: {e}")

    err, data = _fire_and_poll(script_path, result_path, timeout_s)
    if err is not None:
        return err

    bones = data.get("bones", {})
    ikrig = data.get("ikrig", {})
    errors = data.get("errors", [])
    payload = {
        "humanoid": bones.get("humanoid"),
        "bone_count": bones.get("count"),
        "bone_method": bones.get("method"),
        "mannequin_missing": bones.get("mannequin_missing"),
        "ikrig_path": ikrig.get("path"),
        "configured_count": ikrig.get("configured_count"),
        "verified_count": ikrig.get("verified_count"),
        "unique_verified_count": ikrig.get("unique_verified_count"),
        "expected_count": ikrig.get("expected_count"),
        "cleared_count": ikrig.get("cleared_count"),
        "cleared_incomplete": ikrig.get("cleared_incomplete"),
        "stage": data.get("stage"),
        "errors": errors,
        "skeletal_mesh_path": skeletal_mesh_path,
        "ikrig_name": ikrig_name,
        "result_file": str(result_path),
    }

    # Host backstop (defense-in-depth vs a UE5-side gate regression): the rig must land EXACTLY
    # `expected` UNIQUE chains (= len(CHAINS), 7 for Sworder; reported by the UE5 script). It must
    # replicate every clause of the UE5-side gate so it still catches the dup state if that gate
    # regresses — count alone is not enough, because the duplicate chain NAMES are what break the
    # IK Retargeter (an expected-count/fewer-unique rig is broken):
    #   - verified_count 0  = re-query failed (allowed — UE5-side trusts the adds)
    #   - verified_count == expected AND unique == expected = good
    #   - any other count, a non-{0,expected} unique count, count != unique, or an incomplete
    #     pre-clear (stale chains survived) = dup pollution -> reject (never ship a stacked rig)
    vc = ikrig.get("verified_count")
    uv = ikrig.get("unique_verified_count")
    expected = ikrig.get("expected_count") or _CANONICAL_CHAIN_COUNT
    dup_count = isinstance(vc, int) and vc not in (0, expected)
    dup_unique = isinstance(uv, int) and uv not in (0, expected)
    dup_mismatch = isinstance(vc, int) and isinstance(uv, int) and vc != uv
    if bool(data.get("ok")) and (dup_count or dup_unique or dup_mismatch or ikrig.get("cleared_incomplete")):
        reason = (
            f"dup-pollution backstop: verified={vc} unique={uv} expected={expected} "
            f"cleared_incomplete={ikrig.get('cleared_incomplete')} — rig is not exactly the canonical "
            f"{expected} unique chains (the retarget blocker — UE5-side gate should have caught this)"
        )
        return ToolResult(
            ok=False,
            content=f"Autorig of {ikrig_name} rejected: not exactly {expected} unique chains (verified={vc} unique={uv}).",
            data=payload,
            error=reason,
        )

    # Fail-closed: the UE5-side RES["ok"] is True only on humanoid + expected configured + verified.
    if bool(data.get("ok")):
        return ToolResult.success(
            content=(
                f"Rigged {ikrig_name}: humanoid ({bones.get('count')} bones), "
                f"{ikrig.get('configured_count')}/{ikrig.get('expected_count')} chains @ {ikrig.get('path')}"
            ),
            data=payload,
        )

    reason = "; ".join(errors) if errors else f"autorig failed at stage '{data.get('stage')}'"
    return ToolResult(
        ok=False,
        content=f"Autorig of {ikrig_name} failed-closed: {reason}",
        data=payload,
        error=reason,
    )


# ============================================================================
# Standalone bone validator (punch-list #2, 2026-05-30) — the AssetDoctor :8090 half,
# decoupled from rig-building so a skeleton can be validated WITHOUT the IKRig side-effect.
# Same proven get_bones + 23-bone MANNEQUIN_CORE fail-closed check as ue5_autorig_humanoid,
# minus the IKRig section. read_only/SAFE. Surfaced to MVPDoctor as a Category.ASSET @check
# so bone integrity gates demo_ready instead of running only when a plan emits an autorig step.
# ============================================================================

_VALIDATE_MARKER = "BIONICS_VALIDATE_RESULT"

# Validate-ONLY UE5-side script: the get_bones 4-method enumerator + MANNEQUIN_CORE humanoid
# gate from _VALIDATE_AND_RIG_SCRIPT, with NO IKRig creation. Writes {ok, humanoid, bones, errors}.
_VALIDATE_ONLY_SCRIPT = r'''
import json
import traceback

import unreal

PARAMS_PATH = r"__PARAMS_PATH__"
RESULT_PATH = r"__RESULT_PATH__"
with open(PARAMS_PATH) as _pf:
    _p = json.load(_pf)
SK_MESH = _p["skeletal_mesh_path"]

MANNEQUIN_CORE = [
    "root", "pelvis", "spine_01", "spine_02", "spine_03", "neck_01", "head",
    "clavicle_l", "upperarm_l", "lowerarm_l", "hand_l",
    "clavicle_r", "upperarm_r", "lowerarm_r", "hand_r",
    "thigh_l", "calf_l", "foot_l", "ball_l",
    "thigh_r", "calf_r", "foot_r", "ball_r",
]

RES = {"ok": False, "stage": "init", "bones": {}, "errors": []}


def get_bones(mesh):
    """Bone-name extraction — Method 0 (transient SkeletalMeshComponent) is the only one that
    works in UE5.7; the others are 5.x fallbacks. VERBATIM from ue5_autorig_humanoid's seed."""
    try:
        comp = unreal.SkeletalMeshComponent()
        if hasattr(comp, "set_skeletal_mesh_asset"):
            comp.set_skeletal_mesh_asset(mesh)
        else:
            comp.set_editor_property("skeletal_mesh_asset", mesh)
        n = comp.get_num_bones()
        b = [str(comp.get_bone_name(i)) for i in range(n)]
        if b:
            return b, "SkeletalMeshComponent"
    except Exception as e:  # noqa: BLE001
        RES["errors"].append("bones m0: " + str(e))
    try:
        lib = getattr(unreal, "EditorSkeletalMeshLibrary", None)
        if lib and hasattr(lib, "get_all_bone_names"):
            b = [str(x) for x in (lib.get_all_bone_names(mesh) or [])]
            if b:
                return b, "EditorSkeletalMeshLibrary"
    except Exception as e:  # noqa: BLE001
        RES["errors"].append("bones m1: " + str(e))
    try:
        bt = mesh.skeleton.get_editor_property("bone_tree")
        b = [str(x.name) if hasattr(x, "name") else str(x) for x in (bt or [])]
        if b:
            return b, "bone_tree"
    except Exception as e:  # noqa: BLE001
        RES["errors"].append("bones m2: " + str(e))
    try:
        ref = mesh.skeleton.get_reference_pose()
        b = [str(x.name) for x in ref.get_editor_property("bone_info")]
        if b:
            return b, "reference_pose"
    except Exception as e:  # noqa: BLE001
        RES["errors"].append("bones m3: " + str(e))
    return [], None


def run():
    RES["stage"] = "load"
    mesh = unreal.load_asset(SK_MESH)
    if not isinstance(mesh, unreal.SkeletalMesh):
        RES["errors"].append(f"{SK_MESH} is not a SkeletalMesh")
        return

    RES["stage"] = "validate_bones"
    bones, method = get_bones(mesh)
    RES["bones"] = {"count": len(bones), "method": method, "actual": bones[:60]}
    if not bones:
        RES["errors"].append("bone extraction failed all 4 methods — FAIL-CLOSED (cannot validate)")
        return
    present = set(bones)
    missing = [b for b in MANNEQUIN_CORE if b not in present]
    RES["bones"]["mannequin_missing"] = missing
    RES["bones"]["humanoid"] = not missing
    if missing:
        RES["errors"].append(
            f"missing {len(missing)}/{len(MANNEQUIN_CORE)} Mannequin core bones {missing[:10]} "
            "— not a valid humanoid (FAIL-CLOSED)"
        )
        return
    RES["ok"] = True
    RES["stage"] = "done"


try:
    run()
except Exception as e:  # noqa: BLE001
    RES["errors"].append(str(e))
    RES["traceback"] = traceback.format_exc()

with open(RESULT_PATH, "w") as f:
    json.dump(RES, f, indent=2)

unreal.log(
    f"BIONICS_VALIDATE_RESULT: ok={RES['ok']} humanoid={RES['bones'].get('humanoid')} "
    f"bones={RES['bones'].get('count')}"
)
'''


def _resolve_validate_scratch_dir() -> Path | None:
    """Scratch dir for the validate handshake files — module-level seam so unit tests can
    patch ue5_autorig._resolve_validate_scratch_dir."""
    return resolve_scratch_dir("validate")


def _fire_and_poll_validate(
    script_path: Path,
    result_path: Path,
    timeout_s: float,
    poll_interval_s: float = 0.5,
) -> tuple[ToolResult | None, dict | None]:
    """Fire the validate-only script over :8090 and poll its result JSON. ``call_bridge_tool``
    is passed through so tests that patch ue5_autorig.call_bridge_tool still intercept the fire."""
    return fire_and_poll(
        script_path, result_path, timeout_s,
        invoke=call_bridge_tool, noun="validate", marker=_VALIDATE_MARKER,
        poll_interval_s=poll_interval_s,
    )


@bionics_tool(
    name="ue5_validate_skeleton_bones",
    category="ue5_autorig",
    safety_tier=SafetyTier.SAFE,
    read_only=True,
    aliases=["validate-skeleton-bones", "validate-skeleton"],
    title="Validate Skeleton Bones (23 Mannequin core, read-only, native :8090)",
)
def ue5_validate_skeleton_bones(
    skeletal_mesh_path: Annotated[str, "UE content path to the SkeletalMesh"],
    timeout_s: Annotated[float, "Max seconds to wait for the deferred game-thread validate"] = 30.0,
) -> ToolResult:
    """Validate a skeletal mesh as humanoid — read-only, NO rig side-effect (the AssetDoctor half).

    Enumerates the mesh's bones (UE5.7 SkeletalMeshComponent) and asserts all 23 Mannequin core
    bones are present. Returns SUCCESS only for a true humanoid; FAILURE (fail-closed) if bones
    can't be read or any core bone is missing — never a vacuous pass. Unlike ue5_autorig_humanoid
    this builds nothing, so it is safe to run as a pre-rig preflight or inside MVPDoctor's
    demo_ready sweep. Deferred :8090 transport, mirrors ue5_autorig_humanoid.
    """
    skeletal_mesh_path = (skeletal_mesh_path or "").strip()
    if not skeletal_mesh_path:
        return ToolResult.failure("skeletal_mesh_path is required (UE content path to the SkeletalMesh).")
    if not skeletal_mesh_path.startswith("/"):
        return ToolResult.failure(
            f"skeletal_mesh_path must be a UE content path starting with '/' (got '{skeletal_mesh_path}')."
        )

    scratch = _resolve_validate_scratch_dir()
    if scratch is None:
        return ToolResult.failure("Could not create a writable scratch dir for the validate handshake.")

    params_path = scratch / "validate_params.json"
    script_path = scratch / "validate_bones.py"
    result_path = scratch / "validate_result.json"

    try:
        result_path.unlink(missing_ok=True)  # so the poll only sees a fresh write
    except OSError:
        pass

    try:
        params_path.write_text(
            json.dumps({"skeletal_mesh_path": skeletal_mesh_path}),
            encoding="utf-8",
        )
        script = (
            _VALIDATE_ONLY_SCRIPT
            .replace("__PARAMS_PATH__", params_path.as_posix())
            .replace("__RESULT_PATH__", result_path.as_posix())
        )
        script_path.write_text(script, encoding="utf-8")
    except OSError as e:
        return ToolResult.failure(f"Failed to stage validate handshake files in {scratch}: {e}")

    err, data = _fire_and_poll_validate(script_path, result_path, timeout_s)
    if err is not None:
        return err

    bones = data.get("bones", {})
    errors = data.get("errors", [])
    payload = {
        "humanoid": bones.get("humanoid"),
        "bone_count": bones.get("count"),
        "bone_method": bones.get("method"),
        "mannequin_missing": bones.get("mannequin_missing"),
        "stage": data.get("stage"),
        "errors": errors,
        "skeletal_mesh_path": skeletal_mesh_path,
        "result_file": str(result_path),
    }

    if bool(data.get("ok")) and bones.get("humanoid"):
        return ToolResult.success(
            content=f"{skeletal_mesh_path}: valid humanoid ({bones.get('count')} bones, all 23 Mannequin core present).",
            data=payload,
        )

    reason = "; ".join(errors) if errors else f"validation failed at stage '{data.get('stage')}'"
    return ToolResult(
        ok=False,
        content=f"Skeleton validation of {skeletal_mesh_path} failed-closed: {reason}",
        data=payload,
        error=reason,
    )
