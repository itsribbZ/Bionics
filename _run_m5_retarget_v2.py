"""_run_m5_retarget_v2.py - M5 bake, CORRECTED for the real UE5.7 API.

Old ue5_batch_retarget used the removed IKRetargetBatchOperationNameRule. UE5.7's
duplicate_and_retarget(assets, source_mesh, target_mesh, ik_retarget_asset, search, replace,
prefix, suffix, include_referenced, overwrite) takes meshes directly. Runs over the native
:8090 deferred path (handles the long game-thread batch). Non-destructive (new Anims folder).
ASCII-only.
"""
import sys, os, json, pathlib

BIONICS = r"C:\Users\jbro1\Desktop\T1\Bionics"
PROJ = r"C:\Users\jbro1\Documents\Sworder721\MyProject"
sys.path.insert(0, BIONICS)
os.chdir(BIONICS)

inst = pathlib.Path(PROJ) / ".bionics-bridge" / "instance.json"
if inst.exists():
    try:
        tok = json.loads(inst.read_text()).get("token")
        if tok:
            os.environ["BIONICS_BRIDGE_TOKEN"] = tok
    except Exception:
        pass

from bionics_tools._ue5_native_exec import fire_and_poll, resolve_scratch_dir
from bionics_tools.ue5_native import call_bridge_tool

scratch = resolve_scratch_dir("bake")
script_path = scratch / "bake.py"
result_path = scratch / "bake_result.json"
try:
    result_path.unlink(missing_ok=True)
except OSError:
    pass

PY = r'''
import json, unreal, traceback
RESULT_PATH = r"__RESULT__"
out = {"ok": False, "count_source": 0, "results_count": 0, "errors": [], "outputs": []}
try:
    RTG_PATH = "/Game/Test/Skel/RTG_Mannequin_to_SW_HumanoidTemplate"
    TGT_MESH = "/Game/Test/Skel/SK_SW_HumanoidTemplate/SkeletalMeshes/SK_SW_HumanoidTemplate"
    SRC_FOLDER = "/Game/Characters/Mannequins/Anims/Unarmed"
    TGT_FOLDER = "/Game/Test/Skel/SK_SW_HumanoidTemplate/Anims"

    rtg = unreal.load_asset(RTG_PATH)
    tgt_mesh = unreal.load_asset(TGT_MESH)
    out["rtg_loaded"] = rtg is not None
    out["tgt_mesh_loaded"] = tgt_mesh is not None

    # derive source mesh from the retargeter's source IK rig, else fall back to a Mannequin mesh
    src_mesh = None
    for prop in ("source_ik_rig_asset", "source_ik_rig"):
        try:
            sr = rtg.get_editor_property(prop)
            if sr:
                pm = sr.get_editor_property("preview_skeletal_mesh")
                if pm:
                    src_mesh = pm; out["src_mesh_from"] = "rtg." + prop; break
        except Exception:
            pass
    if src_mesh is None:
        for cand in ("/Game/Characters/Mannequins/Meshes/SKM_Manny_Simple",
                     "/Game/Characters/Mannequins/Meshes/SK_Mannequin",
                     "/Game/Characters/Mannequins/Meshes/SKM_Quinn_Simple"):
            m = unreal.load_asset(cand)
            if m:
                src_mesh = m; out["src_mesh_from"] = cand; break
    out["src_mesh_loaded"] = src_mesh is not None
    out["src_mesh"] = src_mesh.get_path_name() if src_mesh else None

    ar = unreal.AssetRegistryHelpers.get_asset_registry()
    flt = unreal.ARFilter(package_paths=[SRC_FOLDER], recursive_paths=True, class_names=["AnimSequence"])
    assets = list(ar.get_assets(flt))
    out["count_source"] = len(assets)

    if rtg and tgt_mesh and src_mesh and assets:
        batch = unreal.IKRetargetBatchOperation()
        results = batch.duplicate_and_retarget(
            assets, src_mesh, tgt_mesh, rtg,
            "", "", "", "_SWHT", True, True,
        )
        out["results_count"] = len(results) if results else 0
        try:
            out["outputs"] = [str(r) for r in (results or [])][:40]
        except Exception:
            pass
        out["ok"] = out["results_count"] > 0
    else:
        out["errors"].append("missing prereq: rtg=%s tgt=%s src=%s assets=%d" % (
            bool(rtg), bool(tgt_mesh), bool(src_mesh), len(assets)))
except Exception as e:
    out["errors"].append(str(e)); out["traceback"] = traceback.format_exc()

with open(RESULT_PATH, "w") as f:
    json.dump(out, f, indent=2)
unreal.log("BIONICS_BAKE_RESULT: ok=" + str(out["ok"]) + " results=" + str(out["results_count"]) + " src=" + str(out["count_source"]))
'''.replace("__RESULT__", result_path.as_posix())
script_path.write_text(PY, encoding="utf-8")

err, data = fire_and_poll(
    script_path, result_path, 180.0,
    invoke=call_bridge_tool, noun="bake", marker="BIONICS_BAKE_RESULT",
)
print("BAKE_TRANSPORT_ERR:", (err.content if err else None))
print("BAKE_RESULT_FILE:", str(result_path))
if data:
    print("BAKE_OK:", data.get("ok"))
    print("BAKE_SRC_COUNT:", data.get("count_source"))
    print("BAKE_RESULTS_COUNT:", data.get("results_count"))
    print("BAKE_SRC_MESH:", data.get("src_mesh"), "(from", data.get("src_mesh_from"), ")")
    print("BAKE_ERRORS:", data.get("errors"))
