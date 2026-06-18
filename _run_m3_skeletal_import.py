"""_run_m3_skeletal_import.py - M3 STEP 2 (pivotal unlock).

Import SK_SW_HumanoidTemplate.glb as a real SkeletalMesh over the :8090 bridge via
uasvc (ue5_uasvc_import_skeletal). Fail-closed on is_skeletal. First skeletal asset
ever in engine. Run with UE5 + BionicsBridge live. ASCII-only prints (cp1252).
"""
import sys, os, json, pathlib

BIONICS = r"C:\Users\jbro1\Desktop\T1\Bionics"
PROJ = r"C:\Users\jbro1\Documents\Sworder721\MyProject"
GLB = r"C:\Users\jbro1\Desktop\T1\blend-master\bridge\checkpoints\SK_SW_HumanoidTemplate.glb"

sys.path.insert(0, BIONICS)
os.chdir(BIONICS)

# Bridge token (rotates on editor restart) - export for call_bridge_tool auth.
inst = pathlib.Path(PROJ) / ".bionics-bridge" / "instance.json"
if inst.exists():
    try:
        tok = json.loads(inst.read_text()).get("token")
        if tok:
            os.environ["BIONICS_BRIDGE_TOKEN"] = tok
            print("M3_IMPORT: token set from instance.json")
    except Exception as e:
        print("M3_IMPORT: token read err:", e)

print("M3_IMPORT: glb exists =", os.path.exists(GLB), "|", GLB)

from bionics_tools.ue5_uasvc import ue5_uasvc_import_skeletal

res = ue5_uasvc_import_skeletal(
    file_path=GLB,
    asset_name="SK_SW_HumanoidTemplate",
    dest_path="/Game/Test/Skel",
    timeout_s=120.0,
)

ok = getattr(res, "ok", None)
data = getattr(res, "data", None) or {}
print("M3_IMPORT_OK:", ok)
print("M3_IMPORT_CONTENT:", getattr(res, "content", None))
print("M3_IMPORT_IS_SKELETAL:", data.get("is_skeletal"))
print("M3_IMPORT_SKELETON:", data.get("skeleton_path"))
print("M3_IMPORT_IMPORTED:", data.get("imported"))
print("M3_IMPORT_USED_OPTIONS:", data.get("used_options"))
print("M3_IMPORT_ERRORS:", data.get("errors"))
print("M3_IMPORT_RESULT:", "PASS" if (ok and data.get("is_skeletal")) else "FAIL")
