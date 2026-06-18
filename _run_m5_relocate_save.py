"""_run_m5_relocate_save.py - persist + relocate the 26 retargeted anims.

duplicate_and_retarget created /Game/<name>_SWHT AnimSequences IN MEMORY (unsaved, at
Content root). This discovers them, moves each into
/Game/Test/Skel/SK_SW_HumanoidTemplate/Anims, and SAVES. Any that fail to move are saved
in place (never lose the bake). Native :8090 deferred. ASCII-only.
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

scratch = resolve_scratch_dir("relocate")
script_path = scratch / "relocate.py"
result_path = scratch / "relocate_result.json"
try:
    result_path.unlink(missing_ok=True)
except OSError:
    pass

PY = r'''
import json, unreal
RESULT_PATH = r"__RESULT__"
DEST = "/Game/Test/Skel/SK_SW_HumanoidTemplate/Anims"
out = {"found_at_root": [], "moved": [], "saved_in_place": [], "failed": []}
eal = unreal.EditorAssetLibrary
try:
    if not eal.does_directory_exist(DEST):
        eal.make_directory(DEST)
    ar = unreal.AssetRegistryHelpers.get_asset_registry()
    flt = unreal.ARFilter(package_paths=["/Game"], recursive_paths=False, class_names=["AnimSequence"])
    found = sorted(str(a.asset_name) for a in ar.get_assets(flt) if str(a.asset_name).endswith("_SWHT"))
    out["found_at_root"] = found
    for nm in found:
        old = "/Game/" + nm
        new = DEST + "/" + nm
        try:
            if eal.rename_asset(old, new):
                eal.save_asset(new, only_if_is_dirty=False)
                out["moved"].append(new)
            else:
                eal.save_asset(old, only_if_is_dirty=False)
                out["saved_in_place"].append(old)
        except Exception as e:
            try:
                eal.save_asset(old, only_if_is_dirty=False)
                out["saved_in_place"].append(old + " (move-failed, saved in place: " + str(e) + ")")
            except Exception as e2:
                out["failed"].append(old + " " + str(e2))
except Exception as e:
    out["fatal"] = str(e)
with open(RESULT_PATH, "w") as f:
    json.dump(out, f, indent=2)
unreal.log("BIONICS_RELOCATE_RESULT: moved=%d in_place=%d failed=%d" % (
    len(out["moved"]), len(out["saved_in_place"]), len(out["failed"])))
'''.replace("__RESULT__", result_path.as_posix())
script_path.write_text(PY, encoding="utf-8")

err, data = fire_and_poll(
    script_path, result_path, 120.0,
    invoke=call_bridge_tool, noun="relocate", marker="BIONICS_RELOCATE_RESULT",
)
print("RELOCATE_TRANSPORT_ERR:", (err.content if err else None))
if data:
    print("RELOCATE_FOUND:", len(data.get("found_at_root", [])))
    print("RELOCATE_MOVED:", len(data.get("moved", [])))
    print("RELOCATE_SAVED_IN_PLACE:", len(data.get("saved_in_place", [])))
    print("RELOCATE_FAILED:", data.get("failed"))
    print("RELOCATE_FATAL:", data.get("fatal"))
