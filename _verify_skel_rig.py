"""_verify_skel_rig.py - read-only post-reimport sanity.
Loads the SkeletalMesh + Skeleton + IKRig + Retargeter over :8090 and confirms each
still resolves (i.e. the 10:44 SkeletalMesh re-import did NOT orphan the pre-existing
IKR/RTG). No mutation. ASCII-only.
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
    except Exception as e:
        print("VERIFY: token err:", e)

from bionics_tools._ue5_native_exec import fire_and_poll, resolve_scratch_dir
from bionics_tools.ue5_native import call_bridge_tool

scratch = resolve_scratch_dir("verify")
script_path = scratch / "verify_skel.py"
result_path = scratch / "verify_result.json"
try:
    result_path.unlink(missing_ok=True)
except OSError:
    pass

PY = r'''
import json, unreal
RESULT_PATH = r"__RESULT__"
out = {"assets": {}}
paths = {
 "skeletalmesh": "/Game/Test/Skel/SK_SW_HumanoidTemplate/SkeletalMeshes/SK_SW_HumanoidTemplate",
 "skeleton": "/Game/Test/Skel/SK_SW_HumanoidTemplate/SkeletalMeshes/SK_SW_HumanoidTemplate_Skeleton",
 "ikrig": "/Game/Test/Skel/IKR_SW_HumanoidTemplate",
 "retargeter": "/Game/Test/Skel/RTG_Mannequin_to_SW_HumanoidTemplate",
}
for k, p in paths.items():
    try:
        o = unreal.load_asset(p)
        out["assets"][k] = {"loaded": o is not None, "class": (type(o).__name__ if o else None)}
    except Exception as e:
        out["assets"][k] = {"loaded": False, "error": str(e)}
try:
    sm = unreal.load_asset(paths["skeletalmesh"])
    if sm:
        sk = sm.get_editor_property("skeleton")
        out["sm_skeleton_path"] = sk.get_path_name() if sk else None
        out["sm_bone_count"] = len(sk.get_editor_property("bone_tree")) if sk and hasattr(sk, "get_editor_property") else None
except Exception as e:
    out["sm_skeleton_err"] = str(e)
with open(RESULT_PATH, "w") as f:
    json.dump(out, f, indent=2)
unreal.log("BIONICS_VERIFY_RESULT: " + json.dumps(out["assets"]))
'''.replace("__RESULT__", result_path.as_posix())
script_path.write_text(PY, encoding="utf-8")

err, data = fire_and_poll(
    script_path, result_path, 60.0,
    invoke=call_bridge_tool, noun="verify", marker="BIONICS_VERIFY_RESULT",
)
print("VERIFY_TRANSPORT_ERR:", (err.content if err else None))
print("VERIFY_DATA:", json.dumps(data, indent=2) if data else None)
